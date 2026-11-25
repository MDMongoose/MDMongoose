import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api.js'
import { Icon, ICON_NAMES, Tile } from '../components/icons.jsx'
import { Busy, Dialog, Field, useBusy } from '../components/Dialogs.jsx'
import { HELP } from './docs.js'
import { Select } from '../components/Select.jsx'

// Properties card: the node header, its properties grouped by Group / Priority, editing, Change Parent
// and the definitions of NodeType, Action, PropertyType and Enum nodes.

export const LEVELS = ['Reader', 'Modifier', 'Supervisor', 'Manager', 'Governor']
const LVL_STYLE = { Admin: 'b-violet', Governor: 'b-violet', Manager: 'b-green', Supervisor: 'b-green', Modifier: 'b-green', Reader: 'b-blue', Nothing: 'b-grey' }
const PT_BASES = ['UID', 'Number', 'Float', 'String', 'Timestamp', 'Link', 'Enum', 'Array', 'Expression', 'Icon', 'Boolean', 'OwnProperty']
const NEEDS_PARAM = { Link: 'target NodeType, e.g. Customer', Enum: 'enum root, e.g. Countries', Array: 'item NodeType or String', Expression: 'SQL, optional' }

/* ---------------------------------------------------------------- pickers */
// Candidates of a Link / Enum / Array(Node) property from the database, 40 at a time, as Select options.
export const optionsFetcher = (ptype, exclude = []) => async (q, offset, limit) =>
  (await api.options(ptype, q, offset, limit)).filter((o) => !exclude.includes(o.uid))
    .map((o) => ({ value: o.uid, label: o.name, sub: o.uid }))

export function NodePicker({ ptype, onPick, placeholder = '+ Add…', exclude = [] }) {
  return (
    <Select className="in sm dashed" style={{ width: 'auto', minWidth: 120 }} value="" placeholder={placeholder} searchPlaceholder="Search name or UID…"
      emptyText="Type a name or UID" fetchOptions={optionsFetcher(ptype, exclude)}
      onChange={(e) => onPick({ uid: e.target.value, name: e.target.label })} />
  )
}

function ArrayItems({ items, scalar, edit, onChange, onGo }) {
  const move = (i, d) => { const a = items.slice(); const j = i + d; if (j < 0 || j >= a.length) return; [a[i], a[j]] = [a[j], a[i]]; onChange(a) }
  return items.map((it, i) => (
    <li key={(it.uid || it.name) + i} className="chip" style={scalar ? { paddingLeft: 8 } : undefined}>
      <span className="idx">{i + 1}</span>
      {!scalar && <button type="button" className="lnk mono" style={{ fontSize: 12, color: 'var(--ink-3)' }} onClick={() => onGo(it.uid)}>{it.uid}</button>}
      <span className={scalar ? 'mono' : ''}>{it.name}</span>
      {edit && (
        <>
          <button type="button" className="ib ghost" onClick={() => move(i, -1)} aria-label={`Move ${it.name} up`}>↑</button>
          <button type="button" className="ib ghost" onClick={() => move(i, 1)} aria-label={`Move ${it.name} down`}>↓</button>
          <button type="button" className="ib ghost" onClick={() => onChange(items.filter((_, j) => j !== i))} aria-label={`Remove ${it.name}`}>×</button>
        </>
      )}
    </li>
  ))
}

/* ---------------------------------------------------------------- one value */
function Value({ row, value, edit, onChange, onGo }) {
  const base = row.base
  if (base === 'Link' || (base === 'Enum' && !edit)) {
    const it = value && (row.itemsDraft || row.items || [])[0]
    if (edit && base === 'Link') {
      return (
        <div className="row">
          {it ? <span className="chip"><Tile name={it.icon} cat={it.cat} size={18} /><span className="mono faint">{it.uid}</span>{it.name}
            <button type="button" className="ib ghost" onClick={() => onChange(null, [])} aria-label="Clear">×</button></span> : <span className="faint">—</span>}
          <NodePicker ptype={row.ptype} placeholder={it ? 'Change…' : 'Choose…'} onPick={(o) => onChange(o.uid, [{ ...o, icon: 'box', cat: 'entity' }])} />
        </div>
      )
    }
    if (!it) return <span className="faint">—</span>
    return (
      <button type="button" className="chip" onClick={() => onGo(it.uid)} style={base === 'Enum' ? { paddingLeft: 8 } : undefined}>
        {base === 'Link' && <Tile name={it.icon} cat={it.cat} size={18} />}
        <span className="mono faint">{it.uid}</span><span>{it.name}</span>
      </button>
    )
  }
  if (base === 'Enum') {
    return (
      <Select className="in" value={value || ''} onChange={(e) => onChange(e.target.value || null)} placeholder="—"
        searchPlaceholder="Search name or UID…" fetchOptions={async (q, offset, limit) => {
          const got = (await api.options(row.ptype, q, offset, limit)).map((o) => ({ value: o.uid, label: `${o.uid} — ${o.name}` }))
          return offset || q ? got : [{ value: '', label: '—' }, ...got]
        }}
        selectedLabel={(() => { const it = (row.items || []).find((o) => o.uid === value); return it ? `${it.uid} — ${it.name}` : value })()} />
    )
  }
  if (base === 'Array') {
    const scalar = row.arg === 'String' || row.arg === 'OwnProperty'
    const items = scalar ? (value || []).map((v) => ({ name: v })) : (row.itemsDraft || row.items || [])
    const fixedNode = row.name === 'Types'
    return (
      <ol className="arr">
        {fixedNode && <li className="chip" style={{ background: 'var(--bg)' }}><span className="idx">·</span><span>Node</span></li>}
        <ArrayItems items={items} scalar={scalar} edit={edit} onGo={onGo}
          onChange={(a) => scalar ? onChange(a.map((x) => x.name)) : onChange(a.map((x) => x.uid), a)} />
        {items.length === 0 && !edit && <li className="faint" style={{ fontSize: 13 }}>{row.empty_text || (row.name === 'Allowed Child Type' ? 'empty — no own constraint' : '—')}</li>}
        {edit && scalar && row.choices && (
          <li><Select className="in sm dashed" value="" aria-label={`Add to ${row.name}`} onChange={(e) => e.target.value && onChange([...(value || []), e.target.value])}>
            <option value="">+ property</option>{row.choices.filter((c) => !(value || []).includes(c)).map((c) => <option key={c}>{c}</option>)}</Select></li>
        )}
        {edit && scalar && !row.choices && (
          <li><input className="in sm mono dashed" style={{ width: 140 }} placeholder="+ value ↵"
            onKeyDown={(e) => { if (e.key === 'Enter' && e.target.value.trim()) { onChange([...(value || []), e.target.value.trim()]); e.target.value = '' } }} /></li>
        )}
        {edit && !scalar && (
          <li><NodePicker ptype={row.ptype} exclude={value || []} onPick={(o) => onChange([...(value || []), o.uid], [...items, { ...o, icon: 'box', cat: 'entity' }])} /></li>
        )}
      </ol>
    )
  }
  if (base === 'Icon') {
    return (
      <span className="row" style={{ gap: 8 }}>
        <Icon name={value || row.display} size={18} />
        {edit ? (
          <Select className="in" style={{ width: 'auto' }} value={value || ''} onChange={(e) => onChange(e.target.value || null)}>
            <option value="">default of first type</option>
            {ICON_NAMES.map((i) => <option key={i} value={i}>{i}</option>)}
          </Select>
        ) : <><span className="mono">{value || row.display}</span><span className="faint" style={{ fontSize: 12 }}>{row.note}</span></>}
      </span>
    )
  }
  if (base === 'Boolean') {
    if (edit) return <label className="row" style={{ gap: 8 }}><input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} />{value ? 'true' : 'false'}</label>
    return <span style={{ fontWeight: 500, color: value ? 'var(--green)' : 'var(--ink-3)' }}>{value ? '✓ true' : '✗ false'}</span>
  }
  if (base === 'Expression') {
    if (edit) return <textarea className="in mono" rows={2} value={value || ''} onChange={(e) => onChange(e.target.value)} />
    return value ? <code className="mono" style={{ background: 'var(--bg)', borderRadius: 4, padding: '3px 6px' }}>{value}</code> : <span className="faint">—</span>
  }
  if (edit && base === 'OwnProperty' && row.choices) {
    return <Select className="in" value={value || ''} onChange={(e) => onChange(e.target.value)}>
      <option value="">—</option>{row.choices.map((c) => <option key={c}>{c}</option>)}</Select>
  }
  if (edit) {
    return <input className={`in ${['Number', 'Float', 'Timestamp'].includes(base) ? 'mono' : ''}`} value={value ?? ''}
      inputMode={base === 'Number' || base === 'Float' ? 'decimal' : undefined} onChange={(e) => onChange(e.target.value)} />
  }
  const mono = base === 'UID' || base === 'Timestamp' || /Key|Hash|Table Name|Handler|Params|Storage|Function|Mapping/.test(row.name)
  const text = row.display === '' || row.display == null ? '—' : row.display
  return <span className={mono ? 'mono' : ''}>{text}</span>
}

/* ---------------------------------------------------------------- definition editors */
function DefsTable({ defs, edit, onChange, onGo, ptypeUid }) {
  const set = (i, patch) => onChange(defs.map((d, j) => (j === i ? { ...d, ...patch } : d)))
  return (
    <div>
      <div className="minibox" style={{ marginTop: 0 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr><th className="dt">Name</th><th className="dt">PropertyType</th><th className="dt">Parameter</th><th className="dt">Group</th><th className="dt">Priority</th>{edit && <th className="dt" />}</tr></thead>
          <tbody>
            {defs.map((d, i) => edit ? (
              <tr key={i}>
                <td className="dt"><input className="in sm" value={d.name} onChange={(e) => set(i, { name: e.target.value })} /></td>
                <td className="dt"><Select className="in sm" value={d.base} onChange={(e) => set(i, { base: e.target.value, param: '' })}>{PT_BASES.map((p) => <option key={p}>{p}</option>)}</Select></td>
                <td className="dt"><input className="in sm mono" value={d.param || ''} disabled={!NEEDS_PARAM[d.base]} placeholder={NEEDS_PARAM[d.base] || '—'} onChange={(e) => set(i, { param: e.target.value })} /></td>
                <td className="dt"><input className="in sm" value={d.group} onChange={(e) => set(i, { group: e.target.value })} /></td>
                <td className="dt"><input className="in sm mono" style={{ width: 64 }} value={d.priority} onChange={(e) => set(i, { priority: e.target.value })} /></td>
                <td className="dt"><button type="button" className="ib" onClick={() => onChange(defs.filter((_, j) => j !== i))} aria-label="Remove property">×</button></td>
              </tr>
            ) : (
              <tr key={i}>
                <td className="dt">{d.name}</td>
                <td className="dt"><button type="button" className="lnk mono" style={{ fontSize: 12 }} onClick={() => ptypeUid(d.base) && onGo(ptypeUid(d.base))}>{d.base}</button></td>
                <td className="dt mono" style={{ color: 'var(--ink-2)' }}>{d.param || '—'}</td>
                <td className="dt">{d.group}</td>
                <td className="dt mono">{d.priority}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {edit && <button type="button" className="lnk" style={{ fontSize: 12, marginTop: 6 }} onClick={() => onChange([...defs, { name: '', base: 'String', param: '', group: defs.at(-1)?.group || 'General', priority: defs.at(-1)?.priority ?? 10 }])}>+ Add property</button>}
    </div>
  )
}

/* ---------------------------------------------------------------- Steward: one table */
const RANK = { Nothing: 0, Reader: 1, Modifier: 2, Supervisor: 3, Manager: 4, Governor: 5, Admin: 6 }
export function stewardPreview(row, own) {
  const above = Object.fromEntries((row.above || []).map((a) => [a.group, a]))
  const out = []
  if (!own.length) {
    for (const a of row.above || []) out.push({ group: a.group, level: null, eff: a.level, note: `inherited from ${a.from}`, kind: 'inherited' })
    return out
  }
  own.forEach((e, i) => {
    if (row.grants_here) return out.push({ ...e, i, eff: e.level, note: 'granted here', kind: 'own' })
    const a = above[e.group]
    if (!a) return out.push({ ...e, i, eff: 'Nothing', note: 'not granted above — grant it on the root (MDM) first; lists below can only narrow', kind: 'own', warn: true })
    const eff = RANK[e.level] <= RANK[a.level] ? e.level : a.level
    out.push({ ...e, i, eff, kind: 'own', warn: RANK[e.level] > RANK[a.level],
      note: RANK[e.level] > RANK[a.level] ? `capped to ${a.level} by ${a.from}` : RANK[e.level] < RANK[a.level] ? `narrowed from ${a.level}` : `same as above (${a.from})` })
  })
  for (const a of row.above || []) {
    if (!own.some((e) => e.group === a.group)) out.push({ group: a.group, level: null, eff: 'Nothing', note: `${a.level} above, not listed here — no access in this branch`, kind: 'dropped', above: a })
  }
  return out
}

function StewardTable({ row, edit, own, setOwn, isAdmin }) {
  const [g, setG] = useState('')
  const [lvl, setLvl] = useState('Reader')
  const shown = edit ? stewardPreview(row, own) : null
  const add = () => {
    const name = g.trim()
    if (!name) return
    const base = own.length || row.grants_here ? own : (row.above || []).map((a) => ({ group: a.group, level: a.level }))
    setOwn([...base.filter((e) => e.group !== name), { group: name, level: lvl }])
    setG('')
  }
  const known = [...new Set([...(row.known_groups || []), ...(row.above || []).map((a) => a.group)])]
  if (!edit) {
    return (
      <div className="minibox">
        <table className="stew">
          <thead><tr><th className="dt">UC group</th><th className="dt">Effective</th><th className="dt">Source</th></tr></thead>
          <tbody>{(row.perms || []).map((pm) => (
            <tr key={pm.group}>
              <td className="dt mono">{pm.group}</td>
              <td className="dt"><span className={`badge ${LVL_STYLE[pm.level]}`}>{pm.level}</span></td>
              <td className="dt" style={{ color: 'var(--ink-2)' }}>{pm.source}{pm.warn && <span style={{ display: 'block', color: 'var(--red)', fontWeight: 500 }}>{pm.warn}</span>}</td>
            </tr>))}
          </tbody>
        </table>
        {!(row.own || []).length && <div className="pnote" style={{ padding: '6px 10px' }}>{row.empty_text}</div>}
      </div>
    )
  }
  return (
    <div className="minibox">
      <table className="stew">
        <thead><tr><th className="dt">UC group</th><th className="dt">Level here</th><th className="dt">Effective</th><th className="dt">Note</th><th className="dt" /></tr></thead>
        <tbody>
          <tr><td className="dt mono">admins</td><td className="dt faint">—</td><td className="dt"><span className="badge b-violet">Admin</span></td><td className="dt faint">global — all privileges everywhere</td><td className="dt" /></tr>
          {shown.map((r) => (
            <tr key={r.kind + r.group} className={r.kind === 'own' ? 'own' : 'other'}>
              <td className="dt mono">{r.group}</td>
              <td className="dt">{r.kind === 'own'
                ? <Select className="in sm" value={r.level} aria-label={`Level of ${r.group} here`} onChange={(e) => setOwn(own.map((x, j) => (j === r.i ? { ...x, level: e.target.value } : x)))}>
                    {LEVELS.map((l) => <option key={l}>{l}</option>)}</Select>
                : <span className="faint">{r.kind === 'inherited' ? 'inherits' : 'not listed'}</span>}</td>
              <td className="dt"><span className={`badge ${LVL_STYLE[r.eff]}`}>{r.eff}</span></td>
              <td className="dt" style={{ color: r.warn ? 'var(--red)' : 'var(--ink-2)' }}>{r.note}</td>
              <td className="dt" style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                {r.kind === 'own' && <button type="button" className="ib ghost" aria-label={`Remove ${r.group}`} onClick={() => setOwn(own.filter((_, j) => j !== r.i))}>×</button>}
                {r.kind === 'dropped' && <button type="button" className="lnk" style={{ fontSize: 12 }} onClick={() => setOwn([...own, { group: r.group, level: r.above.level }])}>Keep</button>}
              </td>
            </tr>
          ))}
          <tr className="addrow">
            <td className="dt"><input className="in sm mono" style={{ minWidth: 170 }} list="ucgroups" placeholder="UC group name" value={g} onChange={(e) => setG(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add() } }} aria-label="UC group to add" />
              <datalist id="ucgroups">{known.map((k) => <option key={k} value={k} />)}</datalist></td>
            <td className="dt"><Select className="in sm" value={lvl} onChange={(e) => setLvl(e.target.value)} aria-label="Level for the new group">{LEVELS.map((l) => <option key={l}>{l}</option>)}</Select></td>
            <td className="dt" colSpan={2}><button type="button" className="tb" style={{ minHeight: 28 }} disabled={!g.trim()} onClick={add}>+ Add group</button>
              {g.trim() && !known.includes(g.trim()) && <span className="faint" style={{ fontSize: 11, marginLeft: 8 }}>{isAdmin ? 'new group — a Permission node is created on save' : 'new group — only an admin can add it'}</span>}</td>
            <td className="dt" />
          </tr>
        </tbody>
      </table>
      <div className="pnote" style={{ padding: '6px 10px' }}>
        {row.grants_here ? 'This list grants access for the whole branch below.'
          : 'Lists below the first one only narrow: a group must be listed again here and keeps at most its level from above; groups you do not list lose access in this branch. Remove all rows to inherit again.'}
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------- main */
export default function Properties({ node, via, editing, saveRef, onSaved, changeParent, onCloseChangeParent, onMoved, onGo, notify, isAdmin, bar }) {
  const [draft, setDraft] = useState({})
  const [itemsDraft, setItemsDraft] = useState({})
  const [saving, setSaving] = useState(false)
  const [stewDraft, setStewDraft] = useState(null)   // [{group, level}] — own Steward entries being edited
  const [defDraft, setDefDraft] = useState(null)
  const [actDraft, setActDraft] = useState(null)
  const [picked, setPicked] = useState(null)
  const [closed, setClosed] = useState({})
  const [err, setErr] = useState(null)
  const def = node.definition
  const can = node.can || {}
  const canEdit = editing && !!can.edit && !node.locked

  const ptypeUid = (base) => ({ UID: 'MDM-A', Number: 'MDM-B', Float: 'MDM-C', String: 'MDM-D', Timestamp: 'MDM-E', Link: 'MDM-F', Enum: 'MDM-G', Array: 'MDM-H', Expression: 'MDM-I', Icon: 'MDM-J', Boolean: 'MDM-K' }[base])

  const rows = useMemo(() => {
    const out = node.properties.map((p) => ({ ...p }))
    const L = (group, prio, name, base, extra) => out.push({ name, base, ptype: extra?.ptype || base, group, priority: prio, defined_by: [extra?.label || 'definition'], defined_by_uids: [], read_only: true, ...extra })
    if (def?.kind === 'NodeType') {
      L('Definition', 5, 'Default Icon', 'Icon', { value: def.default_icon, display: def.default_icon, label: 'NodeType definition' })
      L('Definition', 5, 'Properties', 'Defs', { ptype: 'Array(PropertyDef)', label: 'NodeType definition', read_only: !def.editable })
      L('Definition', 5, 'Actions', 'Actions', { ptype: 'Array(Action)', label: 'NodeType definition', read_only: !def.editable })
      L('Definition', 5, 'Implemented by', 'Implemented', { ptype: 'Expression(SQL)', label: 'NodeType definition' })
    } else if (def?.kind === 'PropertyType') {
      L('Definition', 5, 'Delta Storage', 'String', { display: def.storage, label: 'PropertyType definition' })
      L('Definition', 5, 'Editor', 'String', { display: def.editor, label: 'PropertyType definition' })
    } else if (def?.kind === 'Action') {
      L('Definition', 5, 'Action Group', 'String', { display: def.action_group, label: 'Action definition' })
      L('Definition', 5, 'Runs for', 'String', { display: `${def.required_level} and above (Required Level)`, label: 'Action definition' })
      L('Definition', 5, 'Params', 'String', { display: def.params, label: 'Action definition' })
      L('Definition', 5, 'Python Handler', 'String', { display: def.handler + (def.mode ? `  ·  runs ${def.mode === 'sync' ? 'in the app' : `as a ${def.mode}`}` : ''), label: 'Action definition' })
      if (def.code) L('Definition', 5, 'Code', 'Code', { label: 'Action definition' })
      L('Definition', 5, 'Assigned To', 'Assigned', { ptype: 'Array(NodeType)', label: 'Action definition' })
    } else if (def?.kind === 'Export') {
      L('Definition', 5, 'Writes to', 'String', { display: def.location ? `${def.export_type} · ${def.location}` : 'set Export Type and Root Node', label: 'Export definition' })
      if (def.sql) L('Definition', 5, 'SQL', 'Sql', { label: 'Export definition' })
    }
    return out
  }, [node, def])

  const groups = useMemo(() => {
    const m = new Map()
    rows.forEach((r, i) => {
      if (!m.has(r.group)) m.set(r.group, { name: r.group, prio: r.priority, rows: [], i })
      const g = m.get(r.group); g.prio = Math.min(g.prio, r.priority); g.rows.push(r)
    })
    return [...m.values()].sort((a, b) => a.prio - b.prio || a.i - b.i)
  }, [rows])

  const defs = defDraft || (def?.property_defs || []).map((d) => ({ name: d.name, base: d.base, param: d.param, group: d.group, priority: d.priority }))
  const acts = actDraft || def?.actions || []

  saveRef.current = async () => {
    if (saving) return
    setErr(null)
    setSaving(true)
    try {
      const changes = { ...draft }
      if (stewDraft) changes.Steward = stewDraft.length ? (await api.resolvePermissions(stewDraft)).uids : []
      if (Object.keys(changes).length) await api.edit(node.uid, changes, node.modified_at)
      if (def?.kind === 'NodeType' && (defDraft || actDraft)) {
        const fresh = Object.keys(draft).length ? await api.node(node.uid) : node
        await api.saveNodeType(node.uid, { property_defs: defs, actions: acts.map((a) => a.uid), expected: fresh.modified_at })
      }
      const any = Object.keys(draft).length || stewDraft || defDraft || actDraft
      onSaved(any ? `Saved the changes of ${node.name}` : 'Nothing to save')
      if (!any) setSaving(false)
    } catch (e) { setErr(e.message); setSaving(false) }
  }
  useEffect(() => { setSaving(false) }, [node])

  const toggleUd = async (p) => {
    const on = !p.user_defined
    try {
      await api.edit(node.uid, {}, node.modified_at, { [p.name]: on })
      onSaved(`${p.name}: UserDefined = ${on}${on ? ' — automatic updates will skip it' : ' — value kept; the next source change will refresh it'}`)
    } catch (e) { notify(e.message, true) }
  }

  const tileCat = node.cat
  const typeList = node.kind === 'NodeType' ? 'NodeType definition' : node.kind === 'PropertyType' ? 'PropertyType definition'
    : node.kind === 'Action' ? 'Action definition' : ['Node', ...node.types.map((t) => t.name)].join(' + ')

  return (
    <>
      <div className="phead">
        <div className="row" style={{ gap: 12, minWidth: 0, flexWrap: 'nowrap' }}>
          <Tile name={node.icon} cat={tileCat} loaded={node.origin === 'loaded'} size={44} />
          <div style={{ minWidth: 0 }}>
            <h1>{node.name}</h1>
            <div className="meta">
              <span className="mono">{node.uid}</span><span aria-hidden="true">·</span><span>{typeList}</span>
              {node.locked && <span className="flag locked">LOCKED</span>}
              {editing && <span className="flag editing">EDITING</span>}
            </div>
          </div>
        </div>
        <div className="faint" style={{ fontSize: 12 }}>modified {String(node.modified_at).slice(0, 16).replace('T', ' ')} · {node.modified_by}</div>
      </div>

      {bar && <div className="pbar">{bar}</div>}
      <Busy show={saving} label="Saving…" />
      {node.link && (
        <div role="note" className="banner">
          <Icon name="link" size={15} />
          {node.link.deleted
            ? <span style={{ flex: '1 1 320px' }}>A <strong>Child Link</strong> whose origin <span className="mono">{node.link.target}</span> was deleted. Delete this link, or restore the origin.</span>
            : <span style={{ flex: '1 1 320px' }}>A <strong>Child Link</strong> ({node.link.link_type}) to <strong>{node.link.name}</strong>{node.link.path.length > 1 && <> in <span style={{ color: 'var(--ink-2)' }}>{node.link.path.slice(0, -1).map((p) => p.name).join(' / ')}</span></>}.{!node.link.readable && ' You cannot read the origin.'}</span>}
          {!node.link.deleted && <button className="tb" style={{ minHeight: 28 }} onClick={() => onGo(node.link.target)}>Go to origin</button>}
        </div>
      )}
      {changeParent && <ChangeParent node={node} onClose={onCloseChangeParent} onMoved={onMoved} />}
      {editing && !canEdit && <div className="banner" style={{ borderColor: '#F2B8B0', background: 'var(--red-bg)', color: 'var(--red)' }}>{node.locked ? 'This node is locked — unlock it to edit.' : `You are ${node.level} here — editing needs Modifier or above.`}</div>}
      {err && <div className="error" style={{ margin: '14px 20px 0' }}>{err}</div>}

      <div className="ptable-wrap">
        <div className="ptable-box">
          <table className="ptable">
            <thead><tr><th style={{ width: '30%' }}>PROPERTY</th><th>VALUE</th></tr></thead>
            <tbody>
              {groups.map((g) => {
                const col = !!closed[g.name]
                return [
                  <tr key={'g:' + g.name} className="sect"><td colSpan={2}>
                    <button onClick={() => setClosed((c) => ({ ...c, [g.name]: !col }))} aria-expanded={!col}>
                      <Icon name={col ? 'chevron' : 'down'} size={13} />
                      <span className="gl">{g.name}</span>
                      <span className="faint" style={{ fontSize: 11 }}>{g.rows.length}</span>
                    </button>
                  </td></tr>,
                  ...(col ? [] : g.rows.map((p) => {
                    const key = p.name + '|' + p.ptype
                    const isPicked = picked === key
                    const shared = (p.defined_by || []).length > 1
                    const gov = p.group === 'Governance' && !can.govern
                    const ed = canEdit && !p.read_only && !gov
                    const value = p.name in draft ? draft[p.name] : p.value
                    const pRow = { ...p, itemsDraft: itemsDraft[p.name] }
                    return (
                      <tr key={key} className={`prow ${isPicked ? 'picked' : ''} ${p.name in draft || (p.name === 'Steward' && stewDraft) ? 'dirty' : ''}`}
                        onClick={(e) => { if (['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON', 'OPTION', 'LABEL'].includes(e.target.tagName) || e.target.closest('button,label,.picker')) return; setPicked(isPicked ? null : key) }}>
                        <td className="pn">
                          <div className="pname">{p.name}
                            {p.from_origin && <span className="faint" title="The origin's value — change it on the origin node" style={{ display: 'inline-flex' }}><Icon name="link" size={12} /></span>}
                            {p.user_defined && <span title="User defined — automatic updates will not overwrite it" style={{ display: 'inline-flex', color: 'var(--accent)' }}><Icon name="pencil" size={12} /></span>}
                          </div>
                          {isPicked && (
                            <>
                              <span className="ptag" style={{ marginTop: 2 }}>{p.ptype}</span>
                              <div className="srcs">
                                {(p.defined_by || []).map((t, i) => (
                                  <button key={t} type="button" className={`srcchip ${shared ? 'shared' : ''}`} title={`Defined by ${t}`}
                                    onClick={() => p.defined_by_uids?.[i] && onGo(p.defined_by_uids[i])}>{t}</button>
                                ))}
                                <span style={{ fontSize: 10.5, color: 'var(--ink-3)' }}>group {p.group} · priority {p.priority}{shared ? ` · shared by ${p.defined_by.length} types` : ''}</span>
                                {p.conflict && <span style={{ fontSize: 10.5, fontWeight: 600, color: 'var(--red)' }}>⚠ same name, different PropertyType</span>}
                              </div>
                              {p.can_user_define !== undefined && (
                                <label className="udbox">
                                  <input type="checkbox" checked={!!p.user_defined} disabled={!p.can_user_define || !can.edit || node.locked || (p.group === 'Governance' && !can.govern)}
                                    onChange={() => toggleUd(p)} style={{ marginTop: 2 }} />
                                  <span><strong style={{ fontWeight: 600 }}>UserDefined</strong> — {!p.can_user_define ? 'not applicable (system-maintained)'
                                    : p.user_defined ? 'user override: automatic updates skip this value. Uncheck to let the next source change refresh it.'
                                      : 'automatic: loads and survivorship may update this value. Editing it sets UserDefined.'}</span>
                                </label>
                              )}
                            </>
                          )}
                        </td>
                        <td>
                          {p.base === 'Defs' ? <DefsTable defs={defs} edit={canEdit && def.editable} onChange={setDefDraft} onGo={onGo} ptypeUid={ptypeUid} />
                            : p.base === 'Actions' ? (
                              <ol className="arr">
                                <ArrayItems items={acts} edit={canEdit && def.editable} onGo={onGo} onChange={setActDraft} />
                                {canEdit && def.editable && <li><NodePicker ptype="Array(Node)" placeholder="+ Add action…" exclude={acts.map((a) => a.uid)} onPick={(o) => setActDraft([...acts, { uid: o.uid, name: o.name }])} /></li>}
                              </ol>)
                              : p.base === 'Implemented' ? <span style={{ fontSize: 13 }}><code className="mono" style={{ background: 'var(--bg)', borderRadius: 4, padding: '3px 6px' }}>count(*) WHERE array_contains(Types, '{node.uid}')</code> = <strong>{def.implemented_by} node(s)</strong></span>
                                : p.base === 'Code' ? <pre className="code">{def.code}</pre>
                                : p.base === 'Sql' ? <pre className="code" style={{ maxHeight: 320, overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{def.sql}</pre>
                                  : p.base === 'Assigned' ? <ol className="arr"><ArrayItems items={def.assigned_to} onGo={onGo} /></ol>
                                      : p.name === 'Steward' && p.own ? <StewardTable row={p} edit={ed} isAdmin={isAdmin}
                                          own={stewDraft || p.own.map((o) => ({ group: o.group, level: o.level }))} setOwn={setStewDraft} />
                                      : <Value row={pRow} value={value} edit={ed} onGo={onGo}
                                        onChange={(v, items) => { setDraft((d) => ({ ...d, [p.name]: v })); if (items) setItemsDraft((x) => ({ ...x, [p.name]: items })) }} />}
                          {p.perms && !p.own && (
                            <div className="minibox">
                              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                                <thead><tr><th className="dt">Effective · UC group</th><th className="dt">Level</th><th className="dt">Source</th></tr></thead>
                                <tbody>{p.perms.map((pm) => (
                                  <tr key={pm.group}>
                                    <td className="dt mono">{pm.group}</td>
                                    <td className="dt"><span className={`badge ${LVL_STYLE[pm.level]}`}>{pm.level}</span></td>
                                    <td className="dt" style={{ color: 'var(--ink-2)' }}>{pm.source}{pm.warn && <span style={{ display: 'block', color: 'var(--red)', fontWeight: 500 }}>{pm.warn}</span>}</td>
                                  </tr>))}
                                </tbody>
                              </table>
                            </div>
                          )}
                          {p.note && p.base !== 'Icon' && <div className="pnote">{p.note}</div>}
                          {p.inherited && !(value || []).length && p.name !== 'Allowed Child Type' && <div className="pnote">inherited: {p.inherited.display} (from {p.inherited.from})</div>}
                        </td>
                      </tr>
                    )
                  })),
                ]
              })}
            </tbody>
          </table>
        </div>
      </div>
    </>
  )
}

/* ---------------------------------------------------------------- change parent */
// A dialog with one dropdown of the possible parents: the current parent is selected; the list holds the nodes
// whose Allowed Child Type accepts this node's types (or that have none), searched in the database, 40 at a time.
function ChangeParent({ node, onClose, onMoved }) {
  const cur = node.path.at(-2)
  const [val, setVal] = useState(cur?.uid || '')
  const [label, setLabel] = useState(cur?.name || '')
  const [busy, err, run] = useBusy()
  const fetchParents = async (q, offset, limit) => (await api.validParents(node.uid, q, offset, limit))
    .map((o) => ({ value: o.uid, label: `${o.name}${o.current ? ' (current parent)' : ''}`, sub: `${o.uid}${o.path ? ' · ' + o.path : ''}` }))
  const apply = () => run(async () => {
    await api.move(node.uid, val)
    onMoved(`Moved ${node.name} under ${label.replace(' (current parent)', '')}`)
  })
  return (
    <Dialog title={`Change Parent · ${node.name}`} size="sm" onClose={onClose} busy={busy} busyLabel="Moving…" help={HELP.change_parent}
      footer={<div className="r"><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn pri" disabled={!val || val === cur?.uid || busy} onClick={apply}>Move</button></div>}>
      <div className="dbody">
        <Field label="New parent" htmlFor="cpsel" hint="only parents that accept this node">
          <Select id="cpsel" className="in" value={val} selectedLabel={label} searchPlaceholder="Search name or UID…" data-autofocus
            fetchOptions={fetchParents} onChange={(e) => { setVal(e.target.value); setLabel(e.target.label) }} />
        </Field>
        {err && <div className="error">{err}</div>}
      </div>
    </Dialog>
  )
}
