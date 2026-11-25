import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { PAGE, api } from '../../core/api.js'
import { Icon, ICON_NAMES } from '../../components/icons.jsx'
import { Busy, Dialog } from '../../components/Dialogs.jsx'
import { editChildrenApi } from './api.js'
import { Select } from '../../components/Select.jsx'
import { HELP } from '../../core/docs.js'

// Edit Children: every child of a node in an Excel-like grid — explode levels, copy and paste with Excel,
// sort and filter over all children, save everything in one transaction.

const MAX_RENDER = 3000
const TRUE_W = ['true', 'yes', 'y', '1', 'x', '✓', 'igen', 'i']
const FALSE_W = ['false', 'no', 'n', '0', '-', 'nem', '']

function parseTSV(text) {
  const rows = []
  let row = [], cur = '', q = false
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (q) { if (ch === '"' && text[i + 1] === '"') { cur += '"'; i++ } else if (ch === '"') q = false; else cur += ch }
    else if (ch === '"' && cur === '') q = true
    else if (ch === '\t') { row.push(cur); cur = '' }
    else if (ch === '\n' || ch === '\r') { if (ch === '\r' && text[i + 1] === '\n') i++; row.push(cur); rows.push(row); row = []; cur = '' }
    else cur += ch
  }
  if (cur !== '' || row.length) { row.push(cur); rows.push(row) }
  return rows.filter((r) => r.some((x) => x !== ''))
}
const tsvCell = (v) => (/[\t\n"]/.test(v) ? `"${String(v).replace(/"/g, '""')}"` : String(v ?? ''))

function validate(col, text) {
  const t = String(text ?? '').trim()
  if (t === '') return null
  switch (col.base) {
    case 'Number': return /^-?\d{1,3}([ , _]?\d{3})*$|^-?\d+$/.test(t) ? null : 'not a whole number'
    case 'Float': return /^-?\d+([.,]\d+)?$|^-?\d{1,3}(,\d{3})*(\.\d+)?$/.test(t.replace(/\s/g, '')) ? null : 'not a number'
    case 'Boolean': return TRUE_W.includes(t.toLowerCase()) || FALSE_W.includes(t.toLowerCase()) ? null : 'use TRUE / FALSE'
    case 'Icon': return ICON_NAMES.includes(t.toLowerCase()) ? null : 'unknown icon'
    case 'Enum': return null   // checked on save (the values are searched in the database, not loaded here)
    case 'Timestamp': return /\d/.test(t) ? null : 'unrecognised date'
    default: return null
  }
}

export default function ChildrenGrid({ node, onClose, notify }) {
  const [levels, setLevels] = useState(1)
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [edits, setEdits] = useState({})          // uid -> {prop: text}
  const [newRows, setNewRows] = useState([])      // [{id, values}]
  const [deletes, setDeletes] = useState(new Set())
  const [serverErr, setServerErr] = useState({})  // rowKey -> {field|'*': msg}
  const [sel, setSel] = useState(null)
  const [checked, setChecked] = useState(new Set())
  const [editing, setEditing] = useState(null)
  const [sort, setSort] = useState(null)
  const [filters, setFilters] = useState({})      // key -> {values:Set|null}
  const [hidden, setHidden] = useState(new Set())
  const [order, setOrder] = useState(null)
  const [menu, setMenu] = useState(null)
  const [fmtOpen, setFmtOpen] = useState(false)
  const [pasteOpen, setPasteOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [busy, setBusy] = useState(false)
  const modifiedRef = useRef({})                  // uid -> modified_at of every loaded row
  const seq = useRef(0)
  const [confirm, setConfirm] = useState(null)
  const [revertAsk, setRevertAsk] = useState(false)
  const hist = useRef({ past: [], future: [], prev: null, skip: true })
  const [histLen, setHistLen] = useState({ undo: 0, redo: 0 })
  const wrap = useRef()
  const nextId = useRef(1)
  const dragCol = useRef(null)

  const srvSort = sort && sort.server ? { name: sort.name, ptype: sort.ptype, dir: sort.dir } : null
  const srvFilters = Object.values(filters).filter((f) => f.server).map((f) => ({ name: f.name, ptype: f.ptype, values: [...f.values] }))
  const srvKey = JSON.stringify([srvSort, srvFilters])

  const query = useCallback((lv, offset, reset) => {
    const my = ++seq.current
    setBusy(true); setErr(null)
    return editChildrenApi.gridQuery(node.uid, { levels: lv, offset, sort: srvSort, filters: srvFilters }).then((d) => {
      if (my !== seq.current) return
      for (const r of d.rows) r.uids.forEach((u, i) => { if (u) modifiedRef.current[u] = r.modified[i] })
      setData((prev) => {
        if (offset === 0 || !prev) return d
        const known = new Set(prev.columns.map((c) => c.key))
        return { ...d, rows: prev.rows.concat(d.rows), columns: prev.columns.concat(d.columns.filter((c) => !known.has(c.key))) }
      })
      if (offset === 0) { setSel(null); setChecked(new Set()); setServerErr({}) }
      if (reset) { hist.current = { past: [], future: [], prev: null, skip: true }; setHistLen({ undo: 0, redo: 0 }); setEdits({}); setNewRows([]); setDeletes(new Set()) }
    }).catch((e) => my === seq.current && setErr(e.message)).finally(() => my === seq.current && setBusy(false))
  }, [node.uid, srvKey])  // eslint-disable-line react-hooks/exhaustive-deps
  const load = useCallback((lv) => query(lv, 0, true), [query])
  const lastLevels = useRef(null)
  useEffect(() => {
    const reset = lastLevels.current !== levels
    lastLevels.current = levels
    query(levels, 0, reset)
  }, [levels, query])
  const hasData = !!data
  useEffect(() => {
    const a = document.activeElement
    if (hasData && wrap.current && (!a || a === document.body || a.classList.contains('dialog'))) wrap.current.focus({ preventScroll: true })
  }, [hasData])
  const loadMore = () => data?.has_more && query(levels, data.next, false)

  const dirty = Object.keys(edits).length > 0 || newRows.length > 0 || deletes.size > 0
  const allCols = data?.columns || []
  const cols = useMemo(() => {
    const ks = order || allCols.map((c) => c.key)
    return ks.map((k) => allCols.find((c) => c.key === k)).filter((c) => c && !hidden.has(c.key))
  }, [allCols, hidden, order])
  const canEdit = data?.can_edit
  const canAdd = canEdit && data?.can_add
  const canDel = canEdit && data?.can_delete
  const govCol = (col) => (col.name === 'Owner' || col.name === 'Steward') && !data?.can_govern

  const allRows = useMemo(() => {
    if (!data) return []
    return data.rows.map((r, i) => ({ key: `s${i}`, src: r, isNew: false }))
      .concat(newRows.map((n) => ({ key: `n${n.id}`, newRow: n, isNew: true })))
  }, [data, newRows])

  const cellValue = useCallback((row, col) => {
    if (row.isNew) return col.level === 1 ? (row.newRow.values[col.name] ?? '') : ''
    const uid = row.src.uids[col.level - 1]
    if (!uid) return ''
    const e = edits[uid]
    return e && col.name in e ? e[col.name] : row.src.values[col.key] ?? ''
  }, [edits])
  const cellDirty = (row, col) => {
    if (row.isNew) return false
    const uid = row.src.uids[col.level - 1]
    return !!(uid && edits[uid] && col.name in edits[uid] && edits[uid][col.name] !== (row.src.values[col.key] ?? ''))
  }
  const undefinedCell = (row, col) => !row.isNew && row.src.undefined?.includes(col.key)
  const cellReadOnly = (row, col) => {
    if (!canEdit || col.read_only || govCol(col) || undefinedCell(row, col)) return true
    if (row.isNew) return col.level !== 1
    const uid = row.src.uids[col.level - 1]
    return !uid || row.src.links[col.level - 1]
  }
  const cellError = (row, col) => {
    const se = serverErr[row.key]
    if (se && (se[col.name] || (se['*'] && col.name === 'Name' && row.isNew))) return se[col.name] || se['*']
    if (row.isNew ? col.level === 1 : (cellDirty(row, col))) return validate(col, cellValue(row, col))
    return null
  }

  const rows = useMemo(() => {
    let out = allRows.filter((r) => cols.every((c) => {
      const f = filters[c.key]
      if (!f || !f.values || f.server) return true
      const v = String(cellValue(r, c))
      if (c.base === 'Array') return v.split(/,\s*/).some((x) => f.values.has(x)) || (v === '' && f.values.has(''))
      return f.values.has(v)
    }))
    if (sort && !sort.server) {
      const c = cols.find((x) => x.key === sort.key)
      if (c) {
        const num = c.base === 'Number' || c.base === 'Float'
        out = [...out].sort((a, b) => {
          const x = cellValue(a, c), y = cellValue(b, c)
          return sort.dir * (num ? (parseFloat(String(x).replace(',', '.')) || 0) - (parseFloat(String(y).replace(',', '.')) || 0) : String(x).localeCompare(String(y), undefined, { numeric: true }))
        })
      }
    }
    return out
  }, [allRows, cols, filters, sort, cellValue])

  const invalidCount = useMemo(() => {
    let n = 0
    for (const r of allRows) for (const c of allCols) if (cellError(r, c)) n++
    return n
  }, [allRows, allCols, edits, serverErr])

  const setCell = (row, col, text) => {
    if (cellReadOnly(row, col)) return
    if (row.isNew) { setNewRows((nr) => nr.map((n) => (n.id === row.newRow.id ? { ...n, values: { ...n.values, [col.name]: text } } : n))); return }
    const uid = row.src.uids[col.level - 1]
    setEdits((e) => {
      const orig = row.src.values[col.key] ?? ''
      const cur = { ...(e[uid] || {}) }
      if (text === orig) delete cur[col.name]; else cur[col.name] = text
      const out = { ...e }
      if (Object.keys(cur).length) out[uid] = cur; else delete out[uid]
      return out
    })
    setServerErr((s) => { if (!s[row.key]) return s; const x = { ...s }; delete x[row.key]; return x })
  }
  const addRow = (values = {}, uid = null) => { const id = nextId.current++; setNewRows((nr) => [...nr, { id, values, uid }]); return id }

  const range = sel && { r0: Math.min(sel.r0, sel.r1), r1: Math.max(sel.r0, sel.r1), c0: Math.min(sel.c0, sel.c1), c1: Math.max(sel.c0, sel.c1) }
  const inSel = (r, c) => range && r >= range.r0 && r <= range.r1 && c >= range.c0 && c <= range.c1

  const fromField = (e) => e.target !== wrap.current && (['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target.tagName) || !!e.target.closest?.('.colmenu'))
  const copy = (e) => {
    if (fromField(e)) return
    if (!range || editing) return
    const cs = cols.slice(range.c0, range.c1 + 1)
    const lines = [cs.map((c) => c.key).join('\t')]
    for (let r = range.r0; r <= range.r1; r++) lines.push(cs.map((c) => tsvCell(cellValue(rows[r], c))).join('\t'))
    e.clipboardData.setData('text/plain', lines.join('\n'))
    e.preventDefault()
    notify(`Copied ${range.r1 - range.r0 + 1} row(s) with header`)
  }

  const applyGrid = (grid, anchor) => {
    const byKey = new Map(), byName = new Map()
    for (const c of allCols) { byKey.set(c.key.toLowerCase(), c); if (c.level === 1) byName.set(c.name.toLowerCase(), c) }
    const head = grid[0].map((h) => byKey.get(h.trim().toLowerCase()) || byName.get(h.trim().toLowerCase()) || null)
    const hasHeader = head.filter(Boolean).length >= Math.max(1, Math.ceil(grid[0].length / 2))
    const body = hasHeader ? grid.slice(1) : grid
    const targetCols = hasHeader ? head : cols.slice(anchor?.c0 ?? 0).slice(0, grid[0].length)
    const uidIdx = targetCols.findIndex((c) => c && c.name === 'UID' && c.level === 1)
    const rowByUid = new Map(allRows.filter((r) => !r.isNew).map((r) => [r.src.uids[0], r]))
    let updated = 0, created = 0
    body.forEach((line, i) => {
      let target = null
      const pastedUid = uidIdx >= 0 && line[uidIdx] ? line[uidIdx].trim().toUpperCase() : null
      if (pastedUid) target = rowByUid.get(pastedUid) || null
      else if (!hasHeader && anchor) target = rows[anchor.r0 + i] || null
      if (target) {
        targetCols.forEach((c, j) => { if (c && c.name !== 'UID' && j < line.length) setCell(target, c, line[j]) })
        updated++
      } else if (canAdd) {
        const values = {}
        targetCols.forEach((c, j) => { if (c && c.level === 1 && c.name !== 'UID' && j < line.length) values[c.name] = line[j] })
        addRow(values, pastedUid); created++
      }
    })
    return { updated, created, mapped: targetCols.filter(Boolean).map((c) => c.name), ignored: hasHeader ? grid[0].filter((_, i) => !head[i]) : [] }
  }
  const paste = (e) => {
    if (fromField(e)) return
    if (!canEdit || editing) return
    const grid = parseTSV(e.clipboardData.getData('text/plain'))
    if (!grid.length) return
    e.preventDefault()
    const r = applyGrid(grid, range)
    notify(`Pasted: ${r.updated} row(s) updated, ${r.created} new`)
  }

  const onKey = (e) => {
    if (fromField(e)) return
    if (editing || !data || !sel) return
    if ((e.ctrlKey || e.metaKey) && e.key === 'a') { setSel({ r0: 0, c0: 0, r1: rows.length - 1, c1: cols.length - 1 }); e.preventDefault(); return }
    const move = (dr, dc) => {
      const r = Math.max(0, Math.min(rows.length - 1, sel.r1 + dr)), c = Math.max(0, Math.min(cols.length - 1, sel.c1 + dc))
      setSel(e.shiftKey && e.key !== 'Tab' ? { ...sel, r1: r, c1: c } : { r0: r, c0: c, r1: r, c1: c }); e.preventDefault()
    }
    if (e.key === 'ArrowDown') return move(1, 0)
    if (e.key === 'ArrowUp') return move(-1, 0)
    if (e.key === 'ArrowLeft') return move(0, -1)
    if (e.key === 'ArrowRight' || e.key === 'Tab') return move(0, e.shiftKey && e.key === 'Tab' ? -1 : 1)
    const row = rows[sel.r1], col = cols[sel.c1]
    if (!row || !col) return
    if (e.key === 'Delete' || e.key === 'Backspace') {
      for (let r = range.r0; r <= range.r1; r++) for (let c = range.c0; c <= range.c1; c++) setCell(rows[r], cols[c], '')
      e.preventDefault(); return
    }
    if (cellReadOnly(row, col)) return
    if (e.key === 'Enter' || e.key === 'F2') { setEditing({ r: sel.r1, c: sel.c1, value: String(cellValue(row, col)) }); e.preventDefault() }
    else if (e.key === ' ' && col.base === 'Boolean') { setCell(row, col, String(cellValue(row, col)).toUpperCase() === 'TRUE' ? 'FALSE' : 'TRUE'); e.preventDefault() }
    else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) { setEditing({ r: sel.r1, c: sel.c1, value: e.key }); e.preventDefault() }
  }
  const commit = (move = 0) => {
    if (!editing) return
    setCell(rows[editing.r], cols[editing.c], editing.value)
    const r = Math.min(rows.length - 1, editing.r + move)
    setEditing(null)
    setSel({ r0: r, c0: editing.c, r1: r, c1: editing.c })
    wrap.current?.focus()
  }

  const targetRows = () => {
    const out = allRows.filter((r) => checked.has(r.key))
    if (out.length) return out
    if (!range) return []
    return rows.slice(range.r0, range.r1 + 1)
  }
  const deleteRows = () => {
    const rs = targetRows()
    setNewRows((nr) => nr.filter((n) => !rs.some((r) => r.isNew && r.newRow.id === n.id)))
    setDeletes((d) => { const out = new Set(d); for (const r of rs) if (!r.isNew) { const u = r.src.uids[0]; out.has(u) ? out.delete(u) : out.add(u) } return out })
    setChecked(new Set())
  }
  useEffect(() => {
    const h = hist.current
    const cur = { edits, newRows, deletes }
    if (h.skip || !h.prev) { h.skip = false; h.prev = cur; return }
    if (h.prev.edits === edits && h.prev.newRows === newRows && h.prev.deletes === deletes) return
    h.past.push(h.prev)
    if (h.past.length > 200) h.past.shift()
    h.future = []
    h.prev = cur
    setHistLen({ undo: h.past.length, redo: 0 })
  }, [edits, newRows, deletes])
  const restore = (from, to) => {
    const h = hist.current
    if (!h[from].length) return
    const st = h[from].pop()
    h[to].push(h.prev)
    h.skip = true
    setEditing(null); setServerErr({})
    setEdits(st.edits); setNewRows(st.newRows); setDeletes(st.deletes)
    setHistLen({ undo: h.past.length, redo: h.future.length })
  }
  const undo = () => restore('past', 'future')
  const redo = () => restore('future', 'past')
  useEffect(() => {
    const k = (e) => {
      if (!(e.ctrlKey || e.metaKey) || ['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target?.tagName)) return
      const key = e.key.toLowerCase()
      if (key === 'z' && !e.shiftKey) { e.preventDefault(); undo() }
      else if (key === 'y' || (key === 'z' && e.shiftKey)) { e.preventDefault(); redo() }
    }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  })
  const revert = () => { setEdits({}); setNewRows([]); setDeletes(new Set()); setServerErr({}); setRevertAsk(false) }

  const save = async (andClose = false) => {
    if (invalidCount) { setErr(`${invalidCount} invalid cell(s) — fix them before saving`); return false }
    setSaving(true); setErr(null)
    const modified = modifiedRef.current
    const changes = Object.entries(edits).map(([uid, values]) => ({ uid, values, expected: modified[uid] }))
    const firstNew = changes.length
    newRows.forEach((n) => changes.push(n.uid ? { uid: n.uid, values: n.values, parent_uid: node.uid } : { values: n.values, parent_uid: node.uid }))
    try {
      const res = await editChildrenApi.saveChildren(node.uid, changes, [...deletes])
      if (res.errors.length) {
        const se = {}
        for (const e of res.errors) {
          if (e.row == null) continue
          const f = e.field || '*', msg = e.field ? e.error.replace(`${e.field}: `, '') : e.error
          if (e.row >= firstNew) { const k = `n${newRows[e.row - firstNew].id}`; se[k] = { ...(se[k] || {}), [f]: msg } }
          else allRows.filter((r) => !r.isNew && r.src.uids.includes(changes[e.row].uid)).forEach((r) => { se[r.key] = { ...(se[r.key] || {}), [f]: msg } })
        }
        setServerErr(se)
        setErr(`${res.errors.length} row(s) have errors — nothing was saved. ${res.errors[0].error}`)
        setSaving(false)
        return false
      }
      notify(`Saved ${res.saved} child node(s) of ${node.name}`)
      setSaving(false)
      if (andClose) onClose(); else load(levels)
      return true
    } catch (e) { setErr(e.message); setSaving(false); return false }
  }
  const tryClose = () => (dirty ? setConfirm('close') : onClose())
  const tryLevels = (lv) => (dirty ? setConfirm(lv) : setLevels(lv))


  useEffect(() => {
    const off = () => setMenu(null)
    if (menu) document.addEventListener('mousedown', off)
    return () => document.removeEventListener('mousedown', off)
  }, [menu])

  const sortSpec = (c, dir) => ({ key: c.key, dir, server: !!c.server_filter, name: c.name, ptype: c.ptype })
  const nNew = newRows.length, nMod = Object.keys(edits).length
  const lt = data?.level_types || {}
  const levelHeads = useMemo(() => {
    const out = []
    cols.forEach((c, i) => { if (!out.length || out.at(-1).level !== c.level) out.push({ level: c.level, span: 1, start: i }); else out.at(-1).span++ })
    return out
  }, [cols])

  return (
    <Dialog title={`Edit Children · ${node.name}`} size="full" onClose={tryClose} busy={busy || saving} busyLabel={saving ? 'Saving…' : 'Loading…'}
      help={HELP.edit_children} sub={<><span className="mono">{node.uid}</span> · {data ? ['Node', ...data.types].join(' + ') : ''}</>}
      footer={<>
        <div className="row" style={{ gap: 8 }}>
          {invalidCount > 0 && <span className="badge b-red">{invalidCount} invalid cell(s)</span>}
          {dirty && <span className="badge b-orange">Unsaved: {nNew} new · {nMod} modified · {deletes.size} deleted</span>}
          {err && <span className="error" style={{ maxWidth: 520 }}>{err}</span>}
        </div>
        <div className="r">
          {revertAsk ? (
            <span role="alertdialog" aria-label="Confirm revert" className="row" style={{ gap: 6, fontSize: 13 }}>Discard {nNew + nMod + deletes.size} change(s)?
              <button className="btn sm" onClick={() => setRevertAsk(false)}>Keep</button>
              <button className="btn sm dng" autoFocus onClick={revert}>Revert</button></span>
          ) : <button className="btn" disabled={!dirty} onClick={() => setRevertAsk(true)}>Revert changes</button>}
          <button className="btn" onClick={tryClose}>Close editor</button>
          <button className="btn pri" disabled={!dirty || saving || invalidCount > 0} onClick={() => save(false)}>{saving ? 'Saving…' : 'Save changes'}</button>
        </div>
      </>}>
      <div className="gtools" role="toolbar" aria-label="Table actions">
        <button className="tb" disabled={!histLen.undo} onClick={undo} title="Undo the last change (Ctrl+Z)" aria-keyshortcuts="Control+Z">↶ Undo</button>
        <button className="tb" disabled={!histLen.redo} onClick={redo} title="Redo (Ctrl+Y or Ctrl+Shift+Z)" aria-keyshortcuts="Control+Y">↷ Redo</button>
        <span style={{ width: 1, height: 22, background: 'var(--line)' }} aria-hidden="true" />
        <button className="tb" disabled={!canAdd} title={canEdit && !canAdd ? 'Adding children needs Manager' : undefined} onClick={() => { addRow(); setTimeout(() => wrap.current?.scrollTo({ top: 1e9 }), 0) }}><Icon name="plus" size={13} />Add child</button>
        <button className="tb" disabled={!canAdd} onClick={() => setPasteOpen(!pasteOpen)}>Paste rows…</button>
        <button className="tb" disabled={!canDel || (!checked.size && !range)} title={canEdit && !canDel ? 'Deleting needs Manager' : undefined} onClick={deleteRows} style={{ color: canDel ? 'var(--red)' : undefined }}>Delete {checked.size ? `${checked.size} row(s)` : 'rows'}</button>
        <span style={{ width: 1, height: 22, background: 'var(--line)' }} aria-hidden="true" />
        <span className="muted" style={{ fontSize: 12 }}>Explode children</span>
        <div className="row" style={{ gap: 0, border: '1px solid var(--input)', borderRadius: 6, overflow: 'hidden' }}>
          {[1, 2, 3].map((l) => <button key={l} onClick={() => tryLevels(l)} style={{ border: 0, borderLeft: l > 1 ? '1px solid var(--input)' : 0, padding: '5px 12px', cursor: 'pointer', background: levels === l ? 'var(--accent-bg)' : '#fff', color: levels === l ? 'var(--accent-ink)' : undefined, fontWeight: levels === l ? 600 : 400 }}>{l === 1 ? 'off' : `${l} levels`}</button>)}
        </div>
        {sort && <button className="tb" onClick={() => setSort(null)}>Clear sort</button>}
        {Object.keys(filters).length > 0 && <button className="tb" onClick={() => setFilters({})}><Icon name="filter" size={12} />Clear {Object.keys(filters).length} filter(s)</button>}
        <span style={{ flex: 1 }} />
        {hidden.size > 0 && <div className="row" style={{ gap: 4, fontSize: 12 }}>Hidden:{[...hidden].map((k) => <button key={k} className="chip" style={{ minHeight: 22, fontSize: 11, padding: '0 6px' }} title="Show column" onClick={() => setHidden((h) => { const x = new Set(h); x.delete(k); return x })}>{allCols.find((c) => c.key === k)?.name} <Icon name="eye" size={11} /></button>)}</div>}
        <button className="tb" aria-expanded={fmtOpen} onClick={() => setFmtOpen(!fmtOpen)}>Paste formats</button>
      </div>
      <div className="ginfo">
        <span>Children: {(lt['1'] || []).join(' ∪ ') || '—'}</span>
        {levels > 1 && <span>· Their children: {(lt['2'] || []).join(' ∪ ') || '—'}</span>}
        <span className="row" style={{ gap: 5 }}><span className="hatch" />= not defined by that row’s Types (add the type to fill it)</span>
        {levels > 1 && <span><strong>Exploded:</strong> one line per grandchild; parent cells repeat in grey and edit the parent. Key columns stay editable, UIDs are read-only.</span>}
      </div>
      {fmtOpen && (
        <div className="panel">
          <table className="fmt">
            <thead><tr><td>PropertyType</td><td>Copied as (Ctrl+C)</td><td>Accepted on paste (Ctrl+V)</td></tr></thead>
            <tbody>
              <tr><td className="mono">UID</td><td className="mono">MDM-25Q6</td><td>never written — a known UID updates that row (wherever you paste), an unknown UID creates a new row with a new UID</td></tr>
              <tr><td className="mono">String</td><td className="mono">ACME GmbH</td><td>any text</td></tr>
              <tr><td className="mono">Number</td><td className="mono">1200</td><td><span className="mono">1200</span>, <span className="mono">1 200</span>, <span className="mono">1,200</span></td></tr>
              <tr><td className="mono">Float</td><td className="mono">1500.5</td><td><span className="mono">1500.5</span> or <span className="mono">1500,5</span> (decimal comma)</td></tr>
              <tr><td className="mono">Timestamp</td><td className="mono">2026-11-15 09:30</td><td>ISO <span className="mono">2026-11-15T09:30:00Z</span> (offset → UTC) · <span className="mono">2026.11.15.</span> · <span className="mono">15.11.2026</span> · <span className="mono">15/11/2026</span> or <span className="mono">11/15/2026</span> (day-first when ambiguous) · <span className="mono">15 Nov 2026</span>, <span className="mono">2026. november 15.</span> (EN/DE/HU) · <span className="mono">20261115</span> (SAP) · Excel serial · Unix seconds</td></tr>
              <tr><td className="mono">Boolean</td><td className="mono">TRUE / FALSE</td><td>empty = FALSE · <span className="mono">TRUE/FALSE</span>, <span className="mono">yes/no</span>, <span className="mono">1/0</span>, <span className="mono">igen/nem</span></td></tr>
              <tr><td className="mono">Icon</td><td className="mono">building</td><td>icon name; empty = type default</td></tr>
              <tr><td className="mono">Enum(X)</td><td className="mono">Germany</td><td>the value’s Name (case-insensitive) or its UID</td></tr>
              <tr><td className="mono">Link(X)</td><td className="mono">Berlin</td><td>node Name or UID; if a Name is not unique, use the UID</td></tr>
              <tr><td className="mono">Array(NodeType)</td><td className="mono">Germany, Austria</td><td>comma- or semicolon-separated Names or UIDs, kept in that order</td></tr>
              <tr><td className="mono">Array(String)</td><td className="mono">key account, eu</td><td>comma-separated; quote a value that contains a comma: <span className="mono">"Smith, J", eu</span></td></tr>
              <tr><td className="mono">Expression(SQL)</td><td>the expression</td><td>stored as text</td></tr>
            </tbody>
          </table>
          <div className="faint" style={{ fontSize: 12, marginTop: 6 }}>Values that don’t parse or resolve are kept as typed and marked red until fixed; Save is blocked while any remain.</div>
        </div>
      )}
      {pasteOpen && <PasteRows allCols={allCols} onCancel={() => setPasteOpen(false)} onCreate={(grid) => { const r = applyGrid(grid, null); setPasteOpen(false); notify(`Paste rows: ${r.created} new, ${r.updated} updated`) }} />}
      {!data && !err && <div className="empty">Loading…</div>}
      {!data && err && <div className="error" style={{ margin: 16 }}>{err}</div>}
      {data && (
        <div className="gwrap" tabIndex={0} ref={wrap} data-autofocus onFocus={(e) => { if (e.target === wrap.current && !sel && rows.length) { const c = Math.max(0, cols.findIndex((x) => x.name === 'Name')); setSel({ r0: 0, c0: c, r1: 0, c1: c }) } }} onKeyDown={onKey} onCopy={copy} onPaste={paste} role="grid" aria-label={`Children of ${node.name}`} aria-multiselectable="true">
          <table className="grid">
            <thead>
              {levels > 1 && (
                <tr>
                  <th className="rn lvlhead" />
                  {levelHeads.map((h) => <th key={h.level} colSpan={h.span} className={`lvlhead ${h.start > 0 ? 'lvl-start' : ''}`}>L{h.level} · {h.level === 1 ? 'children' : h.level === 2 ? 'their children' : 'below'} — {(lt[String(h.level)] || []).join(' ∪ ')}</th>)}
                </tr>
              )}
              <tr>
                <th className="rn" style={{ top: levels > 1 ? 23 : 0 }}>
                  <div className="row" style={{ gap: 4, flexWrap: 'nowrap', padding: '6px 0' }}>
                    <input type="checkbox" aria-label="Tick all rows" checked={allRows.length > 0 && checked.size === allRows.length} onChange={(e) => setChecked(e.target.checked ? new Set(allRows.map((r) => r.key)) : new Set())} />
                    <button className="lnk" style={{ fontSize: 11, color: 'var(--ink-3)' }} title="Select all cells" onClick={() => { setSel({ r0: 0, c0: 0, r1: rows.length - 1, c1: cols.length - 1 }); wrap.current.focus() }}>#</button>
                  </div>
                </th>
                {cols.map((c, ci) => {
                  const sortHere = sort?.key === c.key
                  return (
                    <th key={c.key} style={{ top: levels > 1 ? 23 : 0 }} className={ci > 0 && cols[ci - 1].level !== c.level ? 'lvl-start' : ''} title={`${c.ptype} · defined by ${c.defined_by.join(', ')}`}
                      draggable onDragStart={() => { dragCol.current = c.key }} onDragOver={(e) => e.preventDefault()}
                      onDrop={() => { const from = dragCol.current; if (!from || from === c.key) return; const ks = (order || allCols.map((x) => x.key)).filter((k) => k !== from); ks.splice(ks.indexOf(c.key), 0, from); setOrder(ks) }}
                      aria-sort={sortHere ? (sort.dir > 0 ? 'ascending' : 'descending') : 'none'}>
                      <div className="h">
                        <button className="sortbtn" title="Sort" onClick={() => setSort(sortHere ? (sort.dir > 0 ? sortSpec(c, -1) : null) : sortSpec(c, 1))}>
                          <span>{c.name}</span><span style={{ color: 'var(--accent)' }}>{sortHere ? (sort.dir > 0 ? '▲' : '▼') : ''}</span>
                        </button>
                        <button className={`hb ${filters[c.key] ? 'on' : ''}`} aria-haspopup="dialog" aria-label={`Sort and filter ${c.name}`} onMouseDown={(e) => e.stopPropagation()}
                          onClick={(e) => { const b = e.currentTarget.getBoundingClientRect(), w = wrap.current.getBoundingClientRect(); setMenu({ key: c.key, x: Math.min(b.left - w.left + wrap.current.scrollLeft, wrap.current.scrollLeft + w.width - 270), y: b.bottom - w.top + wrap.current.scrollTop }) }}>
                          <Icon name="filter" size={11} />
                        </button>
                      </div>
                      <div className="h2"><span className="ptag">{c.ptype}</span>{c.read_only && <span className="faint" style={{ fontSize: 10, fontWeight: 400 }}>read-only</span>}</div>
                    </th>
                  )
                })}
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, MAX_RENDER).map((r, ri) => {
                const del = !r.isNew && deletes.has(r.src.uids[0])
                const mod = !r.isNew && r.src.uids.some((u) => u && edits[u])
                const prev = ri > 0 ? rows[ri - 1] : null
                const deepest = r.isNew ? 0 : r.src.uids.reduce((m, u, i) => (u ? i : m), 0)
                const isChecked = checked.has(r.key)
                return (
                  <tr key={r.key} className={`${r.isNew ? 'new' : ''} ${mod ? 'mod' : ''} ${del ? 'del' : ''} ${isChecked ? 'rowsel' : ''}`}
                    title={serverErr[r.key]?.['*'] || ''} aria-selected={isChecked}>
                    <td className="rn" title="Click to select the row · Shift+click for more rows"
                      onMouseDown={(e) => { if (e.target.tagName === 'INPUT') return; e.preventDefault(); wrap.current.focus(); setSel(e.shiftKey && sel ? { ...sel, c0: 0, r1: ri, c1: cols.length - 1 } : { r0: ri, c0: 0, r1: ri, c1: cols.length - 1 }) }}>
                      <div className="row" style={{ gap: 6, flexWrap: 'nowrap' }}>
                        <input type="checkbox" checked={isChecked} aria-label={`Select row ${ri + 1}`} onChange={() => setChecked((s) => { const x = new Set(s); x.has(r.key) ? x.delete(r.key) : x.add(r.key); return x })} />
                        <span style={{ color: r.isNew ? 'var(--green)' : serverErr[r.key] ? 'var(--red)' : undefined }}
                          title={r.isNew && r.newRow.uid ? `${r.newRow.uid} is not loaded — saved as an update if it is below this node, otherwise as a new child` : undefined}>
                          {r.isNew ? (r.newRow.uid ? r.newRow.uid : 'new') : ri + 1}</span>
                      </div>
                    </td>
                    {cols.map((c, ci) => {
                      const isEd = editing && editing.r === ri && editing.c === ci
                      const ro = cellReadOnly(r, c)
                      const v = cellValue(r, c)
                      const und = undefinedCell(r, c)
                      const bad = cellError(r, c)
                      const rep = !r.isNew && prev && !prev.isNew && c.level - 1 < deepest && prev.src.uids[c.level - 1] && prev.src.uids[c.level - 1] === r.src.uids[c.level - 1]
                      const cls = [ro && !und ? 'ro' : '', und ? 'undef' : '', rep ? 'rep' : '', inSel(ri, ci) ? 'sel' : '', sel && sel.r1 === ri && sel.c1 === ci ? 'cur' : '',
                        cellDirty(r, c) ? 'dirty' : '', bad ? 'bad' : '', ci > 0 && cols[ci - 1].level !== c.level ? 'lvl-start' : ''].join(' ')
                      return (
                        <td key={c.key} className={cls} role="gridcell" aria-invalid={!!bad} aria-selected={inSel(ri, ci) || false}
                          title={bad || (und ? `${c.name} is not defined by this row’s Types` : rep ? 'parent repeated — editing changes the parent' : '')}
                          onMouseDown={(e) => { if (isEd) return; wrap.current.focus(); if (e.shiftKey && sel) setSel({ ...sel, r1: ri, c1: ci }); else setSel({ r0: ri, c0: ci, r1: ri, c1: ci }) }}
                          onMouseEnter={(e) => { if (e.buttons === 1 && sel && !editing) setSel({ ...sel, r1: ri, c1: ci }) }}
                          onDoubleClick={() => !ro && (c.base === 'Boolean' ? setCell(r, c, String(v).toUpperCase() === 'TRUE' ? 'FALSE' : 'TRUE') : setEditing({ r: ri, c: ci, value: String(v) }))}>
                          {isEd ? (c.base === 'Enum' ? (
                            <Select autoFocus value={editing.value} selectedLabel={editing.value} searchPlaceholder={`Search ${c.arg}…`}
                              fetchOptions={async (q, offset, limit) => {
                                const got = (await api.options(c.ptype, q, offset, limit)).map((o) => ({ value: o.name, label: o.name, sub: o.uid }))
                                return offset || q ? got : [{ value: '', label: '—' }, ...got]
                              }}
                              onChange={(e) => setEditing({ ...editing, value: e.target.value })} onBlur={() => commit()}
                              onKeyDown={(e) => { if (e.key === 'Enter') commit(1); if (e.key === 'Escape') { setEditing(null); wrap.current.focus() } }} />
                          ) : c.base === 'Icon' ? (
                            <Select autoFocus value={editing.value} onChange={(e) => setEditing({ ...editing, value: e.target.value })} onBlur={() => commit()}
                              onKeyDown={(e) => { if (e.key === 'Enter') commit(1); if (e.key === 'Escape') { setEditing(null); wrap.current.focus() } }}>
                              <option value="">—</option>
                              {ICON_NAMES.map((n) => <option key={n} value={n}>{n}</option>)}
                            </Select>
                          ) : (
                            <input autoFocus value={editing.value} onChange={(e) => setEditing({ ...editing, value: e.target.value })} onBlur={() => commit()}
                              placeholder={c.base === 'Array' ? 'comma-separated' : ''}
                              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); commit(1) } if (e.key === 'Escape') { setEditing(null); wrap.current.focus() } if (e.key === 'Tab') { e.preventDefault(); commit(0) } }} />
                          )) : <div className="c">{und ? '' : c.base === 'Boolean' && !r.isNew && r.src.uids[c.level - 1] ? (String(v).toUpperCase() === 'TRUE' ? '✓ TRUE' : 'FALSE') : v}</div>}
                        </td>
                      )
                    })}
                  </tr>
                )
              })}
              {canAdd && (
                <tr className="ghost">
                  <td className="rn"><span aria-label="New row" style={{ color: 'var(--ink-5)' }}>+</span></td>
                  {cols.map((c) => <td key={c.key} onDoubleClick={() => { if (c.level === 1 && !c.read_only) addRow() }}><div className="c" style={{ cursor: 'pointer' }} onClick={() => c.level === 1 && !c.read_only && c.name === 'Name' && addRow()}>{c.name === 'Name' ? 'new child…' : ''}</div></td>)}
                </tr>
              )}
            </tbody>
          </table>
          {rows.length > MAX_RENDER && <div className="note blue" style={{ margin: 8 }}>Showing the first {MAX_RENDER} of {rows.length} rows — filter to narrow down.</div>}
          {data.has_more && (
            <div className="gmore">
              <button className="btn sm" onClick={loadMore} disabled={busy}>Load next {Math.min(PAGE, data.total - data.next)}</button>
              <span>{data.next} of {data.total} children loaded{levels > 1 ? ' (with their children)' : ''}</span>
            </div>
          )}
          {menu && <ColumnMenu node={node} col={allCols.find((c) => c.key === menu.key)} menu={menu} allRows={allRows} cellValue={cellValue} filters={filters} setFilters={setFilters}
            sort={sort} setSort={setSort} sortSpec={sortSpec} onHide={(k) => setHidden((h) => new Set([...h, k]))} onClose={() => setMenu(null)} />}
        </div>
      )}
      <div className="gstatus">
        <span>{data ? `${data.next} of ${data.total} children loaded` : ''}{data && data.rows.length !== data.next ? ` · ${data.rows.length} row(s)` : ''}{rows.length !== allRows.length ? ` · ${rows.length} shown` : ''}</span>
        {nNew > 0 && <span className="badge b-green">{nNew} new</span>}
        {nMod > 0 && <span className="badge b-orange">{nMod} modified</span>}
        {deletes.size > 0 && <span className="badge b-red">{deletes.size} to delete</span>}
        {data && !canEdit && <span className="badge b-orange">read-only</span>}
        {data?.allowed_child_types?.length > 0 && <span>new rows are typed: {data.allowed_child_types.map((t) => t.name).join(', ')}</span>}
        <span style={{ marginLeft: 'auto' }}>Ctrl+Z undo · Ctrl+Y redo · Ctrl+C copies with header · Ctrl+V pastes from Excel · double-click or type to edit · drag a header to move a column</span>
      </div>
      {confirm != null && (
        <div className="confirm">
          <div className="dialog" role="alertdialog" aria-modal="true" aria-labelledby="ucTitle">
            <div className="dhead"><div><h1 id="ucTitle">Discard unsaved changes?</h1><div className="sub">{confirm === 'close' ? 'If you close the editor now, these changes will be lost.' : 'Changing the explode level reloads the table.'} Nothing has been saved to the hierarchy yet.</div></div></div>
            <ul style={{ padding: '14px 24px' }}>
              <li><span className="dot" style={{ background: 'var(--green-bg)', border: '1px solid var(--green-line)' }} /><strong>{nNew}</strong> new row(s)</li>
              <li><span className="dot" style={{ background: '#FFF1DE', border: '1px solid var(--orange-line)' }} /><strong>{nMod}</strong> modified row(s)</li>
              <li><span className="dot" style={{ background: 'var(--red-bg)', border: '1px solid #F2B8B0' }} /><strong>{deletes.size}</strong> deleted row(s)</li>
              {invalidCount > 0 && <li><span className="dot" style={{ background: '#FDECEC', border: '1px solid #E59B8E' }} /><strong>{invalidCount}</strong> invalid cell(s) — fix them before saving</li>}
            </ul>
            <div className="dfoot"><div className="r">
              <button className="btn" autoFocus onClick={() => setConfirm(null)}>Keep editing</button>
              <button className="btn dng" onClick={() => { const c = confirm; setConfirm(null); revert(); if (c === 'close') onClose(); else setLevels(c) }}>Discard{confirm === 'close' ? ' & close' : ''}</button>
              <button className="btn pri" disabled={invalidCount > 0 || saving} onClick={async () => { const c = confirm; setConfirm(null); const ok = await save(c === 'close'); if (ok && c !== 'close') setLevels(c) }}>Save{confirm === 'close' ? ' & close' : ''}</button>
            </div></div>
          </div>
        </div>
      )}
    </Dialog>
  )
}

function ColumnMenu({ node, col, menu, allRows, cellValue, filters, setFilters, sort, setSort, sortSpec, onHide, onClose }) {
  const server = !!col.server_filter
  const [search, setSearch] = useState('')
  const [vals, setVals] = useState(null)      // [{value, label, count}] — the first 40
  const [loading, setLoading] = useState(false)
  const t = useRef()
  const local = useMemo(() => {
    if (server) return []
    const m = new Map()
    for (const r of allRows) {
      const v = String(cellValue(r, col))
      const items = col.base === 'Array' && v ? v.split(/,\s*/) : [v]
      for (const x of items) m.set(x, (m.get(x) || 0) + 1)
    }
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0], undefined, { numeric: true })).map(([v, n]) => ({ value: v, label: v, count: n }))
  }, [allRows, col, server])
  const cur = filters[col.key]
  const [chosen, setChosen] = useState(cur ? new Map(cur.values.size ? [...cur.values].map((v) => [v, cur.labels?.[v] ?? v]) : []) : new Map())
  const got = (list, q) => {
    setVals(list)
    if (q) setChosen(new Map(list.map((x) => [x.value, x.label])))
  }
  useEffect(() => {
    const q = search.trim()
    if (!server) { const l = q.toLowerCase(); got(local.filter((x) => !l || String(x.label).toLowerCase().includes(l)).slice(0, PAGE), q); return }
    clearTimeout(t.current)
    setLoading(true)
    t.current = setTimeout(() => editChildrenApi.gridValues(node.uid, { name: col.name, ptype: col.ptype }, q)
      .then((r) => got(r, q)).catch(() => setVals([])).finally(() => setLoading(false)), vals ? 250 : 0)
    return () => clearTimeout(t.current)
  }, [search, server, local])  // eslint-disable-line react-hooks/exhaustive-deps
  const searching = !!search.trim()
  const shownAll = !!vals?.length && vals.every((x) => chosen.has(x.value))
  const topBox = () => {
    if (searching) {
      setChosen((m) => { const o = new Map(m); for (const x of vals || []) shownAll ? o.delete(x.value) : o.set(x.value, x.label); return o })
    } else if (chosen.size) setChosen(new Map())
    else setChosen(new Map((vals || []).map((x) => [x.value, x.label])))
  }
  const apply = () => {
    setFilters((all) => {
      const out = { ...all }
      if (chosen.size === 0) delete out[col.key]
      else out[col.key] = { values: new Set(chosen.keys()), labels: Object.fromEntries(chosen), server, name: col.name, ptype: col.ptype }
      return out
    })
    onClose()
  }
  const toggle = (x) => setChosen((m) => { const o = new Map(m); o.has(x.value) ? o.delete(x.value) : o.set(x.value, x.label); return o })
  return (
    <div className="colmenu" style={{ left: menu.x, top: menu.y }} onMouseDown={(e) => e.stopPropagation()} role="dialog" aria-label={`Sort and filter ${col.name}`}
      onKeyDown={(e) => { e.stopPropagation(); if (e.key === 'Escape') onClose(); if (e.key === 'Enter') apply() }}
      onCopy={(e) => e.stopPropagation()} onPaste={(e) => e.stopPropagation()}>
      <div className="row" style={{ justifyContent: 'space-between' }}><strong>{col.name}</strong><span className="ptag">{col.ptype}</span></div>
      <div className="row" style={{ gap: 6, margin: '8px 0' }}>
        <button className="tb" style={{ minHeight: 26 }} onClick={() => { setSort(sortSpec(col, 1)); onClose() }}>Sort A→Z</button>
        <button className="tb" style={{ minHeight: 26 }} onClick={() => { setSort(sortSpec(col, -1)); onClose() }}>Z→A</button>
        {sort?.key === col.key && <button className="lnk" style={{ fontSize: 12 }} onClick={() => { setSort(null); onClose() }}>Clear sort</button>}
        <button className="lnk" style={{ fontSize: 12, marginLeft: 'auto' }} onClick={() => { onHide(col.key); onClose() }}>Hide</button>
      </div>
      {cur && <button className="lnk" style={{ fontSize: 12 }} onClick={() => { setFilters((a) => { const o = { ...a }; delete o[col.key]; return o }); onClose() }}>Clear filter from “{col.name}”</button>}
      <input className="in sm" type="search" autoFocus placeholder={server ? 'Search all values' : 'Search'} value={search} onChange={(e) => setSearch(e.target.value)} style={{ marginTop: 6 }} />
      <div className="vals" style={{ position: 'relative', minHeight: 60 }}>
        <label><input type="checkbox" checked={searching ? shownAll : chosen.size === 0} onChange={topBox} />
          {searching ? '(Select all search results)' : '(All values)'}</label>
        {[...chosen].filter(([v]) => !(vals || []).some((x) => x.value === v)).map(([v, l]) => (
          <label key={'c:' + v}><input type="checkbox" checked onChange={() => toggle({ value: v, label: l })} /><span>{v === '' ? <span className="faint">(empty)</span> : l}</span></label>
        ))}
        {(vals || []).map((x) => (
          <label key={x.value}><input type="checkbox" checked={chosen.has(x.value)} onChange={() => toggle(x)} />
            <span>{x.value === '' || x.value == null ? <span className="faint">(empty)</span> : x.label}</span><span className="cnt">{x.count}</span></label>
        ))}
        {vals && vals.length === 0 && <div className="faint" style={{ fontSize: 12 }}>No matching values</div>}
        <Busy show={loading || !vals} />
      </div>
      <div className="row" style={{ justifyContent: 'space-between', fontSize: 11, color: 'var(--ink-3)' }}>
        <span>{chosen.size ? `${chosen.size} selected — OK filters to them` : searching ? 'nothing selected' : 'no filter'}{vals && vals.length >= PAGE ? ` · first ${PAGE} ${searching ? 'matches — refine the search' : 'values — search for more'}` : ''}</span>
        <span>{server ? 'all children' : 'loaded rows'}{col.base === 'Array' ? ' · any item matches' : ''}</span>
      </div>
      <div className="row" style={{ justifyContent: 'flex-end', marginTop: 8 }}>
        <button className="tb" onClick={onClose}>Cancel</button>
        <button className="tb pri" disabled={searching && !chosen.size} onClick={apply}>OK</button>
      </div>
    </div>
  )
}

function PasteRows({ allCols, onCancel, onCreate }) {
  const [text, setText] = useState('')
  const grid = useMemo(() => parseTSV(text), [text])
  const byName = new Map(allCols.filter((c) => c.level === 1).map((c) => [c.name.toLowerCase(), c]))
  const byKey = new Map(allCols.map((c) => [c.key.toLowerCase(), c]))
  const head = grid[0] || []
  const mapped = head.filter((h) => byName.has(h.trim().toLowerCase()) || byKey.has(h.trim().toLowerCase()))
  const hasHeader = mapped.length >= Math.max(1, Math.ceil(head.length / 2))
  const rows = hasHeader ? grid.length - 1 : grid.length
  const ignored = hasHeader ? head.filter((h) => !mapped.includes(h)) : []
  return (
    <div className="panel" style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
      <div style={{ flex: '2 1 360px' }}>
        <label htmlFor="ptext" className="lbl" style={{ fontWeight: 500 }}>Paste rows from Excel / CSV (tab-separated). A header row is matched to column names.</label>
        <textarea id="ptext" className="in mono" rows={5} value={text} onChange={(e) => setText(e.target.value)} autoFocus
          placeholder={'Name\tAlpha2\tCurrency\nPoland\tPL\tPLN'} />
      </div>
      <div style={{ flex: '1 1 220px', fontSize: 12, display: 'flex', flexDirection: 'column', gap: 6 }}>
        <div><strong>{rows}</strong> row(s) · <strong>{head.length}</strong> column(s) · header {hasHeader ? 'detected' : 'not detected (columns in grid order)'}</div>
        {hasHeader && <div>Mapped: {mapped.join(', ') || '—'}</div>}
        {ignored.length > 0 && <div style={{ color: 'var(--orange)' }}>Ignored: {ignored.join(', ')}</div>}
        <div className="row" style={{ marginTop: 'auto' }}>
          <button className="tb" onClick={onCancel}>Cancel</button>
          <button className="tb pri" disabled={!rows} onClick={() => onCreate(hasHeader ? grid : [allCols.filter((c) => c.level === 1 && c.name !== 'UID').map((c) => c.key), ...grid])}>Create {rows} children</button>
        </div>
      </div>
    </div>
  )
}
