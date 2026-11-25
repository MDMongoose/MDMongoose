import { useEffect, useMemo, useRef, useState } from 'react'
import { api, PAGE } from './api.js'
import { Icon, Tile } from '../components/icons.jsx'
import { Busy } from '../components/Dialogs.jsx'
import { SearchBox, SearchResults, hitRows, hitTree, useNodeSearch } from '../components/Search.jsx'

// Hierarchy card: up one level, search below the selected node, the path above the parent, then the
// parent, the current level and the children of the selection.

function Row({ item, level, selected, open, parentRow, match, onClick, onHover }) {
  const t = useRef()
  const kc = item.child_count || 0
  const loaded = item.origin === 'loaded'
  return (
    <div role="treeitem" aria-selected={selected} aria-level={level} className={`trow ${selected ? 'sel' : ''} ${match ? 'match' : ''}`}
      style={{ paddingLeft: (level - 1) * 20 + 2 }}>
      <button className="caret" onClick={onClick} tabIndex={-1} aria-hidden="true">
        {kc > 0 && <Icon name={open ? 'down' : 'chevron'} size={13} />}
      </button>
      <button className="nm" onClick={onClick}
        onMouseEnter={() => { if (onHover && !selected) t.current = setTimeout(() => onHover(item.uid), 120) }}
        onMouseLeave={() => clearTimeout(t.current)} onFocus={() => onHover && onHover(item.uid)}
        title={`${item.is_link ? `Child Link (${item.link_type || 'link'}) · ` : ''}${item.uid} · ${item.level || ''}`}>
        <Tile name={item.icon} cat={item.cat} loaded={loaded} />
        {item.is_link && <span style={{ display: 'inline-flex', color: 'var(--accent)' }} title="Child link"><Icon name="link" size={13} /></span>}
        <span className="label" style={{ fontStyle: loaded ? 'italic' : undefined, fontWeight: selected || parentRow || match ? 600 : undefined,
          color: item.is_link ? 'var(--accent-ink)' : undefined }}>{item.name}</span>
        {item.is_link && <span className="lt">{item.link_type || 'link'}</span>}
        {loaded && <span title="Loaded from source" style={{ display: 'inline-flex', color: 'var(--orange)' }}><Icon name="download" size={12} /></span>}
        {item.locked && <span title="Locked" style={{ display: 'inline-flex', color: 'var(--ink-4)' }}><Icon name="lock" size={12} /></span>}
        {item.level === 'Reader' && <span title="Read only for you" style={{ display: 'inline-flex', color: 'var(--ink-4)' }}><Icon name="eye" size={13} /></span>}
        <span style={{ flex: '1 1 auto' }} />
        {!open && kc > 0 && <span className="cnt">{kc}</span>}
      </button>
    </div>
  )
}

function More({ page, shown, onMore, label }) {
  if (!page || !page.has_more) return null
  return (
    <div className="more" style={{ paddingLeft: label }}>
      <button className="lnk" onClick={onMore}>Load next {Math.min(PAGE, page.total - page.next)}</button>
      <span>{shown} of {page.total}</span>
    </div>
  )
}

const merge = (a, b) => { const seen = new Set(a.map((x) => x.uid)); return a.concat(b.filter((x) => !seen.has(x.uid))) }

export default function Hierarchy({ tree, sel, onSelect, loading, onHover, onPage, onManage }) {
  const [sibs, setSibs] = useState(null)   // {items, page} — current level (with extra pages)
  const [kids, setKids] = useState(null)   // {items, page} — children of the selection
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  const shownFor = useRef(null)
  useEffect(() => {
    if (!tree) return
    const same = shownFor.current === tree.current && !tree.pending
    shownFor.current = tree.pending ? null : tree.current
    if (same && ((sibs && sibs.items.length > tree.siblings.length) || (kids && kids.items.length > tree.children.length))) return
    setSibs({ items: tree.siblings, page: tree.siblings_page })
    setKids({ items: tree.children, page: tree.children_page })
    setErr(null)
  }, [tree])  // eslint-disable-line react-hooks/exhaustive-deps

  const parentUid = tree?.parent?.uid
  const search = useNodeSearch(tree?.current, tree?.current_item?.name)
  // the first search results are usually the ones clicked: their property pages load in the background
  useEffect(() => { if (onPage && search.res?.hits?.length) onPage(search.res.hits.slice(0, 5).map((h) => h.uid)) }, [search.res])  // eslint-disable-line react-hooks/exhaustive-deps
  const hits = useMemo(() => (search.filtering ? hitRows(hitTree(search.res)) : null), [search.filtering, search.res])
  const more = (which) => {
    const st = which === 'sib' ? sibs : kids
    const uid = which === 'sib' ? parentUid : tree.current
    setBusy(true); setErr(null)
    api.childrenPage(uid, st.page.next).then((r) => {
      const next = { items: merge(st.items, r.items), page: r }
      if (onPage) onPage(r.items.map((x) => x.uid))
      which === 'sib' ? setSibs(next) : setKids(next)
    }).catch((e) => setErr(e.message)).finally(() => setBusy(false))
  }

  if (!tree || !sibs || !kids) return <><div className="empty">Loading…</div><Busy show /></>
  const via = sel?.via
  // a Child Link is a node of its own (its own children, its own position): selected like any other node
  const isCurrent = (s) => (via ? s.uid === sel.uid : s.uid === tree.current)
  const pick = (it, holder, rel) => () => {
    const src = rel === 'child' ? kids : rel === 'sibling' ? sibs : null
    onSelect(it.uid, null, src && { item: it, rel, list: src.items, page: src.page })
  }
  const upTarget = tree.parent?.uid
  const indent = (lvl) => (lvl - 1) * 20 + 24

  const lv = tree.parent ? 2 : 1
  const levelRows = []
  if (tree.parent) levelRows.push({ item: tree.parent, level: 1, open: true, parentRow: true, holder: null })
  sibs.items.forEach((s) => {
    const holderRow = via ? s.uid === tree.current : isCurrent(s)
    levelRows.push({ item: s, level: lv, open: holderRow, sel: !via && holderRow, holder: tree.parent?.uid, rel: 'sibling' })
    if (holderRow) {
      kids.items.forEach((c) => levelRows.push({ item: c, level: lv + 1, holder: tree.current, sel: via ? isCurrent(c) : false, rel: 'child' }))
      if (kids.page?.has_more) levelRows.push({ more: 'kid', level: lv + 1 })
      if (tree.pending && (tree.current_item?.child_count || 0) > 0) levelRows.push({ wait: true, level: lv + 1 })
    }
  })

  return (
    <>
      <div className="hier-head">
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
          <h2 className="sec">Hierarchy</h2>
          {onManage && <button className="lnk" onClick={onManage} style={{ fontSize: 12, fontWeight: 500 }}>Browse</button>}
        </div>
        {upTarget && (
          <button className="lnk" onClick={() => onSelect(upTarget)} style={{ fontSize: 12, padding: '4px 6px', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            <Icon name="up" size={12} />Up one level
          </button>
        )}
      </div>
      <div className="hier-filter">
        <SearchBox s={search} scopeName={tree.current_item?.name} id="tsearch" />
      </div>
      <SearchResults s={search} compact onPick={(h) => onSelect(h.uid)} />
      {hits && (
        <div role="tree" aria-label="Search results in the hierarchy" className="tree filtered">
          {hits.map((r) => (
            <Row key={r.item.uid} item={r.item} level={r.level + 1} open={r.open} match={r.hit}
              selected={!via && r.item.uid === tree.current} onClick={() => onSelect(r.item.uid)} onHover={onHover} />))}
        </div>
      )}
      {!hits && tree.ancestors.length > 0 && (
        <nav aria-label="Path above parent" className="path">
          <ol>
            {tree.ancestors.map((a) => (
              <li key={a.uid}>
                <span className="ell" aria-hidden="true"><Icon name="ell" size={13} /></span>
                <button onClick={() => onSelect(a.uid)} className="nm" style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', gap: 8, height: 32, border: 0, background: 'none', padding: '0 6px', cursor: 'pointer', textAlign: 'left', color: 'var(--ink-2)' }}>
                  <Tile name={a.icon} cat={a.cat} loaded={a.origin === 'loaded'} />
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.name}</span>
                </button>
              </li>
            ))}
          </ol>
        </nav>
      )}
      {!hits && <div role="tree" aria-label="Parent, current and child level" className="tree">
        {levelRows.map((r, i) => r.wait
          ? <div key="wait" className="more" style={{ paddingLeft: indent(r.level) }} role="status"><span className="spin sm" aria-hidden="true" />Loading children…</div>
          : r.more
          ? <More key={'more:' + i} page={kids.page} shown={kids.page.next} onMore={() => more('kid')} label={indent(r.level)} />
          : <Row key={(r.item.is_link ? 'l:' : '') + r.item.uid + ':' + i} item={r.item} level={r.level} open={r.open}
              parentRow={r.parentRow} selected={!!r.sel} onClick={pick(r.item, r.holder, r.rel)} onHover={onHover} />)}
        <More page={sibs.page} shown={Math.min(sibs.page?.next ?? 0, sibs.page?.total ?? 0)} onMore={() => more('sib')} label={indent(lv)} />
        {err && <div className="error" style={{ margin: 8 }}>{err}</div>}
      </div>}
      <div className="legend">
        <span><span className="sq" />Manual</span>
        <span><span className="sq" style={{ borderStyle: 'dashed', borderColor: 'var(--orange)' }} />Loaded</span>
        <span style={{ color: 'var(--accent)' }}><Icon name="link" size={12} /><span style={{ color: 'var(--ink-3)' }}>Child link</span></span>
        <span><Icon name="lock" size={12} />Locked</span>
        <span><Icon name="eye" size={12} />Read only for you</span>
      </div>
      <Busy show={busy || loading} />
    </>
  )
}
