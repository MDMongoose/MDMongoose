import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, PAGE, viewAsGroup } from '../../core/api.js'
import { clearCache, load, peek, swr } from '../../core/cache.js'
import { Icon, Tile } from '../../components/icons.jsx'
import { Dialog, useBusy } from '../../components/Dialogs.jsx'
import { SearchBox, SearchResults, hitRows, hitTree, useNodeSearch } from '../../components/Search.jsx'
import { browseApi } from './api.js'
import { HELP } from '../../core/docs.js'

// Browse: the whole tree like a file manager — select subtrees with tick boxes (untick a child to leave it
// out), then move, clone, link or delete them; search with "Filter hierarchy"; Go to a node.

const ROOT = 'MDM-1'
const NODE_TYPE = 'MDM-1E'
const fmt = (n) => (n ?? 0).toLocaleString('en')
const levelKey = (uid) => `mh:${viewAsGroup()}:${uid}`

export default function ManageHierarchyDialog({ meta, onClose, onGo, onChanged }) {
  const [rootItem, setRootItem] = useState(null)
  const [levels, setLevels] = useState({})      // uid -> { items, total, next, has_more }
  const [exp, setExp] = useState(() => new Set([ROOT]))
  const [sel, setSel] = useState(() => new Set())
  const [excl, setExcl] = useState(() => new Set())
  const [anchor, setAnchor] = useState(null)
  const [focus, setFocus] = useState(null)
  const [count, setCount] = useState(null)
  const [op, setOp] = useState(null)
  const [msg, setMsg] = useState(null)
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(true)
  const changed = useRef(false)
  const typeName = useMemo(() => Object.fromEntries((meta?.node_types || []).map((t) => [t.uid, t.name])), [meta])

  const scopeUid = focus || ROOT
  const findItem = (u) => (u === ROOT ? rootItem : Object.values(levels).flatMap((l) => l.items).find((i) => i.uid === u))
  const scopeName = findItem(scopeUid)?.name || 'MDM'
  const search = useNodeSearch(scopeUid, scopeName)
  const [fcol, setFcol] = useState(() => new Set())
  const hits = useMemo(() => (search.filtering ? hitTree(search.res) : null), [search.filtering, search.res])
  useEffect(() => setFcol(new Set()), [search.res])

  const parentOf = useMemo(() => {
    const p = {}
    for (const [u, l] of Object.entries(levels)) for (const it of l.items) p[it.uid] = u
    for (const h of search.res?.hits || []) {
      const chain = [...(h.path_uids || []), h.uid]
      chain.forEach((u, i) => { if (i && !p[u]) p[u] = chain[i - 1] })
    }
    return p
  }, [levels, search.res])

  const loadLevel = useCallback((uid) => {
    const cached = swr(levelKey(uid), () => api.childrenPage(uid, 0),
      (d) => setLevels((ls) => ({ ...ls, [uid]: keepExtra(ls[uid], d) })),
      (e) => { if (e.status === 404) setExp((x) => { const n = new Set(x); n.delete(uid); return n }); else setErr(e.message) })
    if (cached) setLevels((ls) => (ls[uid] ? ls : { ...ls, [uid]: cached }))
  }, [])

  useEffect(() => {
    const cached = peek(`tree:${viewAsGroup()}:${ROOT}`)
    if (cached) { setRootItem(cached.current_item); setBusy(false) }
    api.tree(ROOT).then((t) => { setRootItem(t.current_item); setBusy(false) }).catch((e) => { setErr(e.message); setBusy(false) })
    loadLevel(ROOT)
  }, [loadLevel])

  const toggleOpen = (uid) => {
    const e = new Set(exp)
    if (e.has(uid)) e.delete(uid)
    else { e.add(uid); if (!levels[uid]) loadLevel(uid) }
    setExp(e)
  }
  const loadMore = async (uid) => {
    const l = levels[uid]
    try {
      const d = await api.childrenPage(uid, l.next)
      setLevels((ls) => ({ ...ls, [uid]: { ...d, items: dedupe([...ls[uid].items, ...d.items]) } }))
    } catch (e) { setErr(e.message) }
  }

  // ------------------------------------------------------------- selection
  const ancestors = (uid) => { const out = []; let p = parentOf[uid]; while (p) { out.push(p); p = parentOf[p] } return out }
  const isIn = (uid) => { for (let u = uid; u; u = parentOf[u]) { if (sel.has(u)) return true; if (excl.has(u)) return false } return false }
  const hasOut = (uid) => [...excl].some((x) => ancestors(x).includes(uid) && isIn(parentOf[x]))
  const body = () => ({ selected: [...sel], left_out: [...excl] })

  const tick = (uid) => {
    const s = new Set(sel), x = new Set(excl)
    for (const k of [...s]) if (k !== uid && ancestors(k).includes(uid)) s.delete(k)
    for (const k of [...x]) if (k !== uid && ancestors(k).includes(uid)) x.delete(k)
    const inherited = parentOf[uid] ? isIn(parentOf[uid]) : false
    if (isIn(uid)) { s.delete(uid); if (inherited) x.add(uid) } else { x.delete(uid); if (!inherited) s.add(uid) }
    setSel(s); setExcl(x); setAnchor(uid); setMsg(null)
  }
  const visible = []
  const tickRange = (uid) => {
    const a = visible.indexOf(anchor), b = visible.indexOf(uid)
    if (a < 0 || b < 0) return tick(uid)
    const s = new Set(sel), x = new Set(excl)
    for (const k of visible.slice(Math.min(a, b), Math.max(a, b) + 1)) { x.delete(k); if (!(parentOf[k] && isInWith(k, s, x))) s.add(k) }
    setSel(s); setExcl(x); setAnchor(uid)
  }
  const isInWith = (uid, s, x) => { for (let u = parentOf[uid]; u; u = parentOf[u]) { if (s.has(u)) return true; if (x.has(u)) return false } return false }
  const clear = () => { setSel(new Set()); setExcl(new Set()); setCount(null) }

  useEffect(() => {
    if (!sel.size) { setCount(null); return }
    const t = setTimeout(() => browseApi.manageCount(body()).then(setCount).catch((e) => setErr(e.message)), 200)
    return () => clearTimeout(t)
  }, [sel, excl])  // eslint-disable-line react-hooks/exhaustive-deps

  // ------------------------------------------------------------- search
  const reveal = async (h) => {
    const path = [...(h.path_uids || [])]
    try {
      for (const u of path) {
        if (!levels[u] && !peek(levelKey(u))) {
          const d = await load(levelKey(u), () => api.childrenPage(u, 0))
          setLevels((ls) => ({ ...ls, [u]: ls[u] || d }))
        } else if (!levels[u]) setLevels((ls) => ({ ...ls, [u]: peek(levelKey(u)) }))
      }
      const parent = path[path.length - 1]
      if (parent) {
        setLevels((ls) => {
          const l = ls[parent]
          if (!l || l.items.some((i) => i.uid === h.uid)) return ls
          const pinned = { uid: h.uid, name: h.name, icon: h.icon, cat: h.cat, origin: h.origin, types: h.types || [], child_count: 0, pinned: true }
          return { ...ls, [parent]: { ...l, items: [...l.items, pinned] } }
        })
      }
      setExp((e) => new Set([...e, ...path]))
      setFocus(h.uid)
      setTimeout(() => document.getElementById(`mh-${h.uid}`)?.scrollIntoView({ block: 'center' }), 60)
    } catch (e) { setErr(e.message) }
  }

  // ------------------------------------------------------------- rows
  const rows = []
  const walk = (item, level) => {
    const uid = item.uid
    const open = exp.has(uid)
    const kids = levels[uid]
    const hasKids = item.child_count > 0
    visible.push(uid)
    rows.push({ item, level, open, hasKids })
    if (open && kids) {
      for (const k of kids.items) walk(k, level + 1)
      if (kids.has_more) rows.push({ more: true, uid, level: level + 1, shown: kids.items.length, total: kids.total })
    } else if (open && !kids && hasKids) rows.push({ loading: true, uid, level: level + 1 })
  }
  if (hits) {
    for (const r of hitRows(hits, fcol)) {
      visible.push(r.item.uid)
      rows.push({ item: r.item, level: r.level, open: r.open, hasKids: r.branch > 0, hit: r.hit, filtered: true })
    }
  } else if (rootItem) walk(rootItem, 0)

  const finish = (text) => {
    changed.current = true
    clearCache()
    setLevels({}); clear(); setOp(null); setMsg(text)
    for (const u of exp) loadLevel(u)
    api.tree(ROOT).then((t) => setRootItem(t.current_item)).catch(() => {})
  }
  const close = () => { onClose(); if (changed.current) onChanged() }
  const goTo = (uid) => { onGo(uid); if (changed.current) onChanged() }

  const nSel = count?.roots ?? 0
  return (
    <Dialog title="Manage hierarchy" help={HELP.browse} size="full" labelledBy="mhT" onClose={close} busy={busy} busyLabel="Loading…"
      sub={<>Tick a node to select its whole subtree; untick a child to leave it out · <span className="mono">Shift</span>-click a box to tick a range · click a name for <em>Go to</em>, double-click to open the node</>}
      footer={<>
        <div className="r" style={{ gap: 12 }}>
          {sel.size > 0 && <span role="status" className="mh-pill">
            {count ? `Currently ${nSel} ${nSel === 1 ? 'node' : 'nodes'} selected with ${fmt(count.child_elements)} child elements` : 'Counting…'}
            <button className="lnk" onClick={clear}>Clear</button></span>}
          {msg && <span className="ok-text">{msg}</span>}
          {err && <span className="error">{err}</span>}
        </div>
        <div className="r"><button className="btn" onClick={close}>Close</button></div>
      </>}>
      <div className="mh-bar">
        <SearchBox s={search} scopeName={scopeName} id="mhsearch" />
        <button className="btn" onClick={() => (hits ? setFcol(new Set(hits.kids.keys())) : setExp(new Set([ROOT])))}>Collapse all</button>
        <span style={{ flex: '1 1 auto' }} />
        <button className="btn" disabled={!sel.size} onClick={() => setOp('move')}><Icon name="arrowRight" size={13} />Move subtree</button>
        <button className="btn" disabled={!sel.size} onClick={() => setOp('clone')}><Icon name="copy" size={13} />Clone subtree</button>
        <button className="btn" disabled={!sel.size} onClick={() => setOp('link')}><Icon name="link" size={13} />Link subtree</button>
        <button className="btn" disabled={!sel.size} onClick={() => setOp('delete')} style={{ color: 'var(--danger, #A1260F)' }}><Icon name="trash" size={13} />{nSel > 1 ? `Delete ${nSel} selected` : 'Delete subtree'}</button>
      </div>
      <SearchResults s={search} onPick={reveal} />
      <div className="mh-tree" role="tree" aria-label="Hierarchy" aria-multiselectable="true">
        {rows.map((r, i) => {
          if (r.more) return <div key={`more-${r.uid}`} className="mh-more" style={{ paddingLeft: r.level * 22 + 54 }}>
            <button className="lnk" onClick={() => loadMore(r.uid)}>Load next {PAGE}</button>
            <span className="faint">{fmt(r.shown)} of {fmt(r.total)} shown{isIn(r.uid) ? ' · the rest are selected with it' : ''}</span></div>
          if (r.loading) return <div key={`ld-${r.uid}`} className="mh-more faint" style={{ paddingLeft: r.level * 22 + 54 }}>Loading…</div>
          const it = r.item, uid = it.uid, link = false   // a Child Link is selected, moved and deleted like any node
          const own = isIn(uid), explicit = own && sel.has(uid) && !(parentOf[uid] && isIn(parentOf[uid]))
          const partial = own && hasOut(uid)
          return (
            <div key={`${uid}-${i}`} id={`mh-${uid}`} role="treeitem" aria-level={r.level + 1} aria-selected={own}
              aria-expanded={r.hasKids && !link ? r.open : undefined} className={`mh-row ${explicit ? 'explicit' : own ? 'inherited' : ''} ${r.hit ? 'match' : ''}`}>
              <span className="mh-indent" style={{ width: r.level * 22 }} aria-hidden="true" />
              {r.hasKids && !link
                ? <button className="mh-chev" onClick={() => (r.filtered ? setFcol((c) => { const n = new Set(c); n.has(uid) ? n.delete(uid) : n.add(uid); return n }) : toggleOpen(uid))} aria-label={`${r.open ? 'Collapse' : 'Expand'} ${it.name}`}><span style={{ display: 'inline-flex', transform: `rotate(${r.open ? 90 : 0}deg)`, transition: 'transform .12s' }}><Icon name="chevron" size={12} /></span></button>
                : <span className="mh-chev" aria-hidden="true" />}
              {link ? <span className="mh-box-space" aria-hidden="true" />
                : <button className="mh-check" aria-label={`${own ? 'Leave out' : 'Select'} ${it.name}${partial ? ' (some children left out)' : ''}`} aria-pressed={own}
                  onClick={(e) => (e.shiftKey && anchor ? tickRange(uid) : tick(uid))}>
                  <span className={`mh-box ${partial ? 'partial' : own ? 'on' : ''} ${explicit ? '' : 'soft'}`} aria-hidden="true">
                    {own && !partial && <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="4"><path d="M4 12l5 5L20 6" /></svg>}
                    {partial && <span className="dot" />}
                  </span></button>}
              <button className="mh-name" onClick={() => setFocus(uid)} onDoubleClick={() => goTo(uid)} title="Click for Go to · double-click to open">
                <Tile name={link ? 'link' : it.icon} cat={it.cat} loaded={it.origin === 'loaded'} size={20} />
                {it.is_link && <span style={{ display: 'inline-flex', color: 'var(--accent)' }} title="Child Link"><Icon name="link" size={13} /></span>}
                <span className={`mh-ell ${link ? 'it' : ''}`} style={{ fontWeight: explicit ? 600 : 500 }}>{it.name}</span>
                <span className="mono faint" style={{ fontSize: 11 }}>{uid}</span>
                {link && <span className="mh-type">child link</span>}
                {(it.types || []).filter((t) => t !== NODE_TYPE && (typeName[t] || t) !== 'Node').map((t) => <span key={t} className="mh-type">{typeName[t] || t}</span>)}
              </button>
              {focus === uid && <button className="lnk mh-go" onClick={() => goTo(uid)}>Go to<Icon name="arrowRight" size={12} /></button>}
              <span style={{ flex: '1 1 auto' }} />
              <span className="faint mh-count">{r.hasKids || r.filtered ? (it.child_count ? fmt(it.child_count) : '') : ''}</span>
            </div>
          )
        })}
      </div>
      {op && <ManageAction op={op} body={body()} count={count} onClose={() => setOp(null)} onDone={finish} />}
    </Dialog>
  )
}

function keepExtra(old, fresh) {
  if (!old) return fresh
  const extra = old.items.filter((i) => !fresh.items.some((f) => f.uid === i.uid))
  return extra.length ? { ...old, ...fresh, items: [...fresh.items, ...extra], next: Math.max(fresh.next || 0, old.next || 0) } : fresh
}
const dedupe = (items) => { const seen = new Set(); return items.filter((i) => (seen.has(i.uid) ? false : seen.add(i.uid))) }

const TITLES = { move: 'Move subtree', clone: 'Clone subtree', link: 'Link subtree', delete: 'Delete subtree' }
const NOTES = {
  move: 'UIDs stay; the paths of the moved nodes, and of any left-out branches, are rewritten on the server.',
  clone: 'Every copy gets a new UID and is a manual node. Links inside the copied nodes point to the copies; links to nodes outside stay as they are.',
  link: 'The linked nodes keep their real parent and also show under the new parent; edits show in both places.',
  delete: 'Soft delete: the nodes get is_deleted = true; child links to them are removed. Restore through Delta history.',
}

function ManageAction({ op, body, count, onClose, onDone }) {
  const [tq, setTq] = useState('')
  const [parents, setParents] = useState(null)
  const [target, setTarget] = useState(null)
  const [plan, setPlan] = useState(null)
  const [name, setName] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, err, run] = useBusy()
  const needsTarget = op !== 'delete'

  useEffect(() => {
    if (!needsTarget) return
    const t = setTimeout(() => browseApi.manageParents({ ...body, op }, tq).then(setParents).catch(() => setParents([])), 200)
    return () => clearTimeout(t)
  }, [tq])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    browseApi.managePlan(op, { ...body, target: target?.uid, name }).then((p) => {
      setPlan(p)
      if (op === 'clone' && !name && p.roots === 1) setName(`${p.roots_list[0].name} (copy)`)
    }).catch(() => {})
  }, [target])  // eslint-disable-line react-hooks/exhaustive-deps

  const p = plan || {}
  const what = p.roots === 1 ? `“${op === 'clone' && name ? name : p.roots_list?.[0]?.name}”` : `${p.roots ?? count?.roots ?? '…'} nodes`
  const kids = ` with ${fmt(p.child_elements ?? count?.child_elements)} child elements`
  const out = p.left_out_branches ? ` The ${p.left_out_branches} left-out ${p.left_out_branches === 1 ? 'branch' : 'branches'} (${fmt(p.left_out_nodes)} nodes) ` : ''
  const stay = (p.stay_under || []).map((s) => `“${s}”`).join(', ')
  const one = p.left_out_branches === 1
  let sentence = 'Pick the new parent.'
  if (op === 'delete') sentence = `You will soft delete ${what}${kids}.${out ? `${out}${one ? 'moves' : 'move'} up under ${stay} and ${one ? 'stays' : 'stay'}.` : ''}`
  else if (target && op === 'move') sentence = `You will move ${what}${kids} to “${target.name}”.${out ? `${out}${one ? 'stays' : 'stay'} behind under ${stay}.` : ''}`
  else if (target && op === 'clone') sentence = `You will clone ${what}${kids} to “${target.name}”.${out ? `${out}${one ? 'is' : 'are'} not copied.` : ''}`
  else if (target && op === 'link') sentence = `You will add ${fmt(p.link_count)} Child ${p.link_count === 1 ? 'Link' : 'Links'} under “${target.name}”${p.links?.length ? `: ${p.links.map((l) => l.name).join(', ')}${p.link_count > p.links.length ? ', …' : ''}` : ''}. The selection is mirrored: each selected node gets a Child Link under the link of its parent${p.left_out_branches ? '; left-out branches are not linked' : ''}.`
  const ready = op === 'delete' ? confirm.trim() === 'DELETE' && !p.problem : target && plan && !p.problem

  const apply = () => run(async () => {
    const r = await browseApi.manageApply(op, { ...body, target: target?.uid, name })
    const n = r.moved ?? r.cloned ?? r.linked ?? r.deleted
    onDone({ move: `${fmt(n)} nodes moved to ${target?.name}`, clone: `${fmt(n)} nodes cloned to ${target?.name}`,
      link: `${fmt(n)} child link(s) added to ${target?.name}`, delete: `${fmt(n)} nodes deleted` }[op] + (r.kept ? ` · ${fmt(r.kept)} left-out nodes kept` : ''))
  })

  return (
    <Dialog title={`${TITLES[op]}${p.roots > 1 ? ` · ${p.roots} nodes` : p.roots_list?.[0] ? ` · ${p.roots_list[0].name}` : ''}`} size="md" role="alertdialog" help={HELP.browse}
      labelledBy="mhaT" onClose={onClose} busy={busy} busyLabel="Working…"
      footer={<><span />{<div className="r"><button className="btn" onClick={onClose}>Cancel</button>
        <button className={`btn ${op === 'delete' ? 'dng' : 'pri'}`} disabled={!ready || busy} onClick={apply}>
          {{ move: 'Move', clone: 'Clone', link: 'Add links', delete: 'Delete' }[op]}</button></div>}</>}>
      <div className="dbody" style={{ gap: 12 }}>
        {op === 'clone' && p.roots === 1 && <div><label className="lbl" htmlFor="mhn">Name of the copy</label>
          <input id="mhn" className="in" value={name} onChange={(e) => setName(e.target.value)} /></div>}
        {op === 'clone' && p.roots > 1 && <div className="faint" style={{ fontSize: 12.5 }}>The copies keep their names.</div>}
        {needsTarget && <div className="stack" style={{ gap: 6 }}>
          <label className="lbl" htmlFor="mhp">New parent</label>
          <input id="mhp" className="in" type="search" placeholder="Search a parent by UID or name…" value={tq} onChange={(e) => setTq(e.target.value)} data-autofocus />
          <div role="listbox" aria-label="Possible parents" className="mh-parents">
            {(parents || []).map((c) => {
              const on = target?.uid === c.uid
              return <button key={c.uid} role="option" aria-selected={on} aria-disabled={!!c.problem} className={`mh-parent ${on ? 'on' : ''} ${c.problem ? 'off' : ''}`}
                onClick={() => !c.problem && setTarget(c)}>
                <span className={`mh-radio ${on ? 'on' : ''}`} aria-hidden="true" />
                <span style={{ flex: '1 1 auto', minWidth: 0 }}><span className="mh-ell" style={{ display: 'block', fontWeight: 500 }}>{c.name}</span>
                  <span className="mh-ell" style={{ display: 'block', fontSize: 11.5, color: c.problem ? '#A1260F' : 'var(--ink-3)' }}>{c.problem ? `not possible — ${c.problem}` : c.path || 'root'}</span></span>
                <span className="mono faint">{c.uid}</span></button>
            })}
            {parents && !parents.length && <div className="faint" style={{ padding: 10 }}>No match.</div>}
            {!parents && <div className="faint" style={{ padding: 10 }}>Loading…</div>}
          </div></div>}
        <div className={`mh-sentence ${op === 'delete' ? 'dng' : ''}`}>{sentence}</div>
        {p.problem && (op === 'delete' || target) && <div className="error">{p.problem}</div>}
        <div className="faint" style={{ fontSize: 12 }}>{NOTES[op]}</div>
        {op === 'delete' && <div><label className="lbl" htmlFor="mhd">Type <strong>DELETE</strong> to confirm</label>
          <input id="mhd" className="in" value={confirm} onChange={(e) => setConfirm(e.target.value)} /></div>}
        {err && <div className="error">{err}</div>}
      </div>
    </Dialog>
  )
}
