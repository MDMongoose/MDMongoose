import { useState } from 'react'
import { api } from '../core/api.js'
import { Icon, Tile } from './icons.jsx'

// Search box and collapsible results panel, used by the hierarchy card and Browse; with "Filter hierarchy"
// the caller shows only the hits in their tree (hitTree / hitRows).

export function useNodeSearch(scopeUid, scopeName) {
  const [q, setQ] = useState('')
  const [res, setRes] = useState(null)     // {q, scopeUid, hits} | null
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [filter, setFilter] = useState(true)
  const run = async () => {
    const text = q.trim()
    if (!text) { setRes(null); return }
    setBusy(true); setErr(null)
    try { setRes({ q: text, scopeUid, scopeName, hits: await api.search(text, '', scopeUid || '', 50) }) } catch (e) { setErr(e.message) }
    setBusy(false)
  }
  const clear = () => { setQ(''); setRes(null); setErr(null) }
  const filtering = filter && !!res && res.hits.length > 0
  return { q, setQ, res, busy, err, run, clear, close: () => setRes(null), filter, setFilter, filtering }
}

// The hits with their ancestors below the search scope: { tops, nodes: uid -> item (+hit), kids: uid -> [uid] }
export function hitTree(res) {
  if (!res || !res.hits.length) return null
  const nodes = new Map(), kids = new Map(), tops = []
  for (const h of res.hits) {
    const { path_nodes: pn = [], ...self } = h
    const chain = [...pn, { ...self, hit: true }]
    const start = Math.max(0, chain.findIndex((n) => n.uid === res.scopeUid))
    chain.slice(start).forEach((n, i, c) => {
      const had = nodes.get(n.uid)
      nodes.set(n.uid, had ? { ...had, hit: had.hit || !!n.hit } : { ...n })
      if (i === 0) { if (!tops.includes(n.uid)) tops.push(n.uid); return }
      const p = c[i - 1].uid, l = kids.get(p) || []
      if (!l.includes(n.uid)) kids.set(p, [...l, n.uid])
    })
  }
  return { tops, nodes, kids }
}

export function hitRows(t, collapsed) {
  const rows = []
  if (!t) return rows
  const byName = (a, b) => (t.nodes.get(a).name || '').localeCompare(t.nodes.get(b).name || '')
  const walk = (uid, level) => {
    const k = [...(t.kids.get(uid) || [])].sort(byName)
    const open = k.length > 0 && !(collapsed && collapsed.has(uid))
    const item = t.nodes.get(uid)
    rows.push({ item, level, open, branch: k.length, hit: !!item.hit })
    if (open) k.forEach((c) => walk(c, level + 1))
  }
  t.tops.forEach((u) => walk(u, 0))
  return rows
}

export function SearchBox({ s, scopeName, id = 'nsearch' }) {
  return (
    <div className="nsearch">
      <label htmlFor={id} className="nsearch-lbl">Search <strong>{scopeName || '…'}</strong></label>
      <div className="nsearch-box">
        {s.busy ? <span className="spin nsearch-spin" role="status" aria-label="Searching…" /> : <Icon name="search" size={14} />}
        <input id={id} className="in" placeholder="Name or UID" value={s.q} autoComplete="off"
          onChange={(e) => s.setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); s.run() } if (e.key === 'Escape' && s.q) { e.preventDefault(); e.stopPropagation(); s.clear() } }} />
        {s.q && <button type="button" className="nsearch-btn" onClick={s.clear} aria-label="Clear the search"><Icon name="close" size={12} /></button>}
        <button type="button" className="nsearch-btn enter" onClick={s.run} aria-label="Search" title="Search (Enter)" disabled={s.busy}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M20 5v7a3 3 0 01-3 3H5M9 11l-4 4 4 4" /></svg>
        </button>
      </div>
    </div>
  )
}

export function SearchResults({ s, onPick, compact = false }) {
  const [open, setOpen] = useState(true)
  if (s.err) return <div className="error" style={{ margin: '6px 14px' }}>{s.err}</div>
  if (!s.res) return null
  const n = s.res.hits.length
  return (
    <section className={`nresults ${compact ? 'compact' : ''}`} aria-label="Search results">
      <div className="nresults-head">
        <button className="nresults-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
          <span style={{ display: 'inline-flex', transform: `rotate(${open ? 90 : 0}deg)`, transition: 'transform .12s' }}><Icon name="chevron" size={12} /></span>
          <span>{n === 50 ? '50+' : n} {n === 1 ? 'result' : 'results'} for “{s.res.q}” in {s.res.scopeName}</span>
        </button>
        <label className="nresults-filter" title="Show only the results and their parents in the hierarchy">
          <input type="checkbox" checked={s.filter} onChange={(e) => s.setFilter(e.target.checked)} />Filter hierarchy
        </label>
        <button className="nsearch-btn" onClick={s.close} aria-label="Close the results"><Icon name="close" size={12} /></button>
      </div>
      {open && <div className="nresults-list" role="listbox" aria-label="Results">
        {s.res.hits.map((h) => (
          <button key={h.uid} role="option" aria-selected="false" className="nresult" onClick={() => onPick(h)} title={`${h.path} / ${h.name}`}>
            <Tile name={h.icon} cat={h.cat} loaded={h.origin === 'loaded'} size={18} />
            {compact
              ? <span className="nresult-text"><span className="nresult-name">{h.name}</span><span className="nresult-path">{h.path}</span></span>
              : <><span className="nresult-name">{h.name}</span><span className="nresult-path">{h.path}</span>
                <span className="mono faint" style={{ fontSize: 11 }}>{h.uid}</span></>}
          </button>))}
        {!n && <div className="faint" style={{ padding: '8px 10px', fontSize: 12.5 }}>Nothing found.</div>}
      </div>}
    </section>
  )
}
