import { useCallback, useEffect, useRef, useState } from 'react'
import { api, setViewAsGroup } from './api.js'
import { clearCache, loadPair, prefetchPair, preloadMany, seed, swr } from './cache.js'
import { Icon } from '../components/icons.jsx'
import { Busy, RunDialog } from '../components/Dialogs.jsx'
import { featureFor } from '../features/index.js'
import logoUrl from '../assets/mdmongoose-logo.svg'
import { APP_VERSION } from './version.js'
import Hierarchy from './Hierarchy.jsx'
import Properties from './Properties.jsx'
import { Select } from '../components/Select.jsx'

// The main screen: header, hierarchy card, properties card with the node's actions, and the open dialog.
// Edit, Lock and Change Parent work on the screen; every other action opens its feature (features/).

function uidFromHash() {
  const m = window.location.hash.match(/#\/(MDM-[0-9A-Z]+)(?:\/via\/(MDM-[0-9A-Z]+))?/i)
  return m ? { uid: m[1].toUpperCase(), via: m[2] ? m[2].toUpperCase() : null } : null
}

const GROUP_ORDER = ['Self', 'Children', 'Load', 'Matching', 'Survivorship']

export default function App() {
  const [me, setMe] = useState(null)
  const [meta, setMeta] = useState(null)
  const [sel, setSel] = useState(uidFromHash())
  const [tree, setTree] = useState(null)
  const [node, setNode] = useState(null)
  const [treeBusy, setTreeBusy] = useState(false)
  const [nodeBusy, setNodeBusy] = useState(false)
  const [error, setError] = useState(null)
  const [dialog, setDialog] = useState(null)
  const [toast, setToast] = useState(null)
  const [version, setVersion] = useState(0)
  const [asGroup, setAsGroup] = useState('')
  const [editing, setEditing] = useState(false)
  const [changeParent, setChangeParent] = useState(false)
  const saveRef = useRef(null)
  const pendingTree = useRef(null)
  const editingRef = useRef(false)
  editingRef.current = editing

  const notify = useCallback((msg, err = false) => setToast({ msg, err }), [])
  const reload = useCallback(() => { clearCache(); setVersion((v) => v + 1); api.meta().then(setMeta).catch(() => {}) }, [])
  const treeKey = (uid) => `tree:${asGroup}:${uid}`
  const nodeKey = (uid) => `node:${asGroup}:${uid}`
  // hover / the parent of the shown node: its hierarchy and property page, in one background request
  const warm = useCallback((uid) => {
    if (!uid) return
    prefetchPair(`tree:${asGroup}:${uid}`, `node:${asGroup}:${uid}`, () => api.open(uid))
  }, [asGroup])

  useEffect(() => {
    let live = true
    api.prefetch(5).then((r) => { if (live) seedHierarchy(r, asGroup) }).catch(() => {})
    return () => { live = false }
  }, [asGroup, version])
  useEffect(() => {
    api.me().then((m) => { setMe(m); if (!uidFromHash()) setSel({ uid: m.root, via: null }) }).catch((e) => setError(e.message))
    api.meta().then(setMeta).catch(() => {})
  }, [])
  useEffect(() => {
    const onHash = () => { const u = uidFromHash(); if (u) setSel(u) }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  useEffect(() => { setEditing(false); setChangeParent(false) }, [sel?.uid, sel?.via])

  const treeUid = sel ? (sel.via || sel.uid) : null
  useEffect(() => {
    if (!treeUid) return
    let live = true
    // a click: the hierarchy and the property page come in one request (each part refreshes the cached one)
    if (!sel.via) loadPair(treeKey(treeUid), nodeKey(treeUid), () => api.open(treeUid)).catch(() => {})
    const cached = swr(treeKey(treeUid), () => api.tree(treeUid),
      (t) => { if (live) { setTree(t); setTreeBusy(false) } },
      (e) => { if (live) { setError(e.message); setTreeBusy(false) } })
    const pending = pendingTree.current
    pendingTree.current = null
    if (cached) { setTree(cached); setTreeBusy(false) }
    else if (pending && pending.current === treeUid) { setTree(pending); setTreeBusy(false) }
    else setTreeBusy(true)
    return () => { live = false }
  }, [treeUid, version, asGroup])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (tree && !tree.pending) warm(tree.parent?.uid) }, [tree, warm])
  const preloadViews = useCallback((uids) => {
    const ids = [...new Set(uids.filter(Boolean))]
    for (let i = 0; i < ids.length; i += 40) {
      const chunk = ids.slice(i, i + 40)
      preloadMany(chunk.map((u) => `node:${asGroup}:${u}`), (keys) => api.nodeViews(keys.map((k) => k.split(':').pop())),
        (u) => `node:${asGroup}:${u}`)
    }
  }, [asGroup])
  useEffect(() => {
    if (!tree || tree.pending) return
    preloadViews(tree.children.map((c) => c.uid))
    preloadViews(tree.siblings.map((c) => c.uid))
  }, [tree, preloadViews])
  useEffect(() => {
    if (!sel) return
    let live = true
    setError(null)
    const cached = swr(nodeKey(sel.uid), () => api.node(sel.uid),
      (n) => { if (live && !editingRef.current) { setNode(n); setNodeBusy(false) } },
      (e) => { if (live) { setNode(null); setError(e.message); setNodeBusy(false) } })
    if (cached) { setNode(cached); setNodeBusy(false) } else setNodeBusy(true)
    return () => { live = false }
  }, [sel?.uid, version, asGroup])  // eslint-disable-line react-hooks/exhaustive-deps

  const go = (uid, via = null, hint = null) => {
    if (hint && !via && !sel?.via && tree && !tree.pending) {
      const anc = (a) => a && { uid: a.uid, name: a.name, icon: a.icon, cat: a.cat, origin: a.origin }
      pendingTree.current = hint.rel === 'child'
        ? { ancestors: [...tree.ancestors, anc(tree.parent)].filter(Boolean), parent: tree.current_item,
            siblings: hint.list, siblings_page: hint.page, current: uid, current_item: hint.item,
            children: [], children_page: null, pending: true }
        : { ...tree, current: uid, current_item: hint.item, children: [], children_page: null, pending: true }
    }
    const h = via ? `#/${uid}/via/${via}` : `#/${uid}`
    if (window.location.hash !== h) window.location.hash = h
    else setSel({ uid, via })
  }

  const runAction = async (a) => {
    const n = node
    if (a.ui === 'edit') { setEditing((e) => !e); setChangeParent(false); return }
    if (a.ui === 'change_parent') { setChangeParent(true); setEditing(false); return }
    if (a.ui === 'lock') {
      try { const r = await api.lock(n.uid); notify(`${n.name} is ${r.locked ? 'locked' : 'unlocked'}`); reload() } catch (e) { notify(e.message, true) }
      return
    }
    const feature = featureFor(a.ui)
    if (feature) return setDialog({ feature, node: n, action: a })
    if (a.mode === 'job') return setDialog({ node: n, action: a })
    notify(`No UI for action ${a.name}`, true)
  }
  const close = () => setDialog(null)
  const ctx = {
    meta, actions: node?.actions || [], close, notify, reload, go,
    done: (msg) => { close(); if (msg) notify(msg); reload() },
    open: (key, extra = {}) => setDialog({ feature: featureFor(key), node: extra.node || node, action: { ui: key } }),
  }

  const groups = {}
  for (const a of node?.actions || []) (groups[a.group || 'Self'] = groups[a.group || 'Self'] || []).push(a)
  const groupNames = Object.keys(groups).sort((x, y) => (GROUP_ORDER.indexOf(x) + 99) % 99 - (GROUP_ORDER.indexOf(y) + 99) % 99)
  const lvl = me?.is_admin && !asGroup ? 'Admin' : node?.level
  const green = ['#1E6B3A', '#E6F4EA'], violet = ['#4B3A99', '#F1EEFA']
  const pill = { Admin: violet, Governor: violet, Manager: green, Supervisor: green, Modifier: green, Reader: ['#1E3F9E', '#E8EEFC'] }[lvl] || ['#5B6370', '#F1F2F4']
  const persona = asGroup || (me?.is_admin ? 'admins' : (me?.groups || []).filter((g) => g !== 'users')[0] || 'users')
  const crumbs = node ? (sel.via ? [...(tree?.ancestors || []), tree?.parent, { uid: node.uid, name: node.name }].filter(Boolean) : node.path) : []

  const bar = node && (
        <>
      <nav aria-label="Breadcrumb" className="crumbs">
            {crumbs.map((c, i) => i < crumbs.length - 1 ? (
              <span key={c.uid} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                <button className="lnk" style={{ padding: '4px 2px' }} onClick={() => go(c.uid)}>{c.name}</button>
                <Icon name="chevron" size={14} className="faint" />
              </span>
            ) : <span key={c.uid} className="cur" aria-current="page">{c.name}</span>)}
            <span className="permpill" style={{ marginLeft: 'auto', color: pill[0], background: pill[1] }} title="Effective permission on this node">{persona} · {lvl}</span>
          </nav>

          <div role="toolbar" aria-label="Actions of the selected node" className="toolbar">
            {groupNames.map((g) => (
              <div role="group" aria-label={g} className="agroup" key={g}>
                <span className="gl">{g}</span>
                {groups[g].map((a) => {
                  const label = a.ui === 'lock' ? (node.locked ? 'Unlock' : 'Lock') : a.name
                  const title = a.why || undefined   // only why a disabled action cannot run
                  return a.enabled
                    ? <button key={a.uid} className="tb" title={title} aria-pressed={a.ui === 'edit' ? editing : undefined} onClick={() => runAction(a)}
                        style={a.ui === 'edit' && editing ? { background: 'var(--accent-bg)', borderColor: 'var(--accent-line)' } : undefined}>
                        <Icon name="bolt" size={13} />{label}</button>
                    : <span key={a.uid} className="tb off" aria-disabled="true" title={title}>{label}</span>
                })}
              </div>
            ))}
            {editing && (
              <>
                <span style={{ flex: '1 1 0' }} />
                <button className="tb" onClick={() => { setEditing(false); reload() }}>Cancel</button>
                <button className="tb pri" onClick={() => saveRef.current && saveRef.current()}>Save</button>
              </>
            )}
          </div>
        </>
  )

  return (
    <div className="app">
      <header className="hdr">
        <div className="brand" title={`MDMongoose ${APP_VERSION}${me?.environment ? `\nEnvironment: ${me.environment}` : ''}`}>
          <img className="logo" src={logoUrl} alt="" width="34" height="34" />
          <span className="brandtext">
            <span className="name">MDMongoose</span>
            <span className="tagline">MDM without Madness</span>
          </span>
        </div>
        <span style={{ flex: 1 }} />
        {me?.is_admin && (
          <div className="persona">
            <label htmlFor="persona">View as UC group</label>
            <Select id="persona" value={asGroup} onChange={(e) => { setViewAsGroup(e.target.value); setAsGroup(e.target.value) }}>
              <option value="">admins (Admin)</option>
              {(meta?.groups || ['users']).map((g) => <option key={g} value={g}>{g}</option>)}
            </Select>
          </div>
        )}
        {me && <span className="who"><Icon name="user" size={13} /> {me.name}</span>}
      </header>

      {toast && (
        <div role="status" className={`toast ${toast.err ? 'err' : ''}`}>
          <span className="mono" style={{ fontSize: 12 }}>{toast.err ? '!' : '▶'}</span>{toast.msg}
          <button className="lnk" onClick={() => setToast(null)} style={{ marginLeft: 'auto', fontSize: 12, color: 'inherit' }}>Dismiss</button>
        </div>
      )}

      <main className="main">
        <section aria-label="Hierarchy" className="card hier">
          <Hierarchy tree={tree} sel={sel} onSelect={go} loading={treeBusy} onHover={warm} onPage={preloadViews} onManage={() => ctx.open('browse')} />
        </section>
        <section aria-label="Node properties" className="card props">
          {error && <div className="error" style={{ margin: 16 }}>{error}</div>}
          {node && (
            <Properties key={node.uid + ':' + node.modified_at} node={node} via={sel.via ? tree?.current_item : null}
              editing={editing} saveRef={saveRef} onSaved={(msg) => { setEditing(false); notify(msg || 'Saved'); reload() }}
              changeParent={changeParent} onCloseChangeParent={() => setChangeParent(false)}
              onMoved={(m) => { setChangeParent(false); notify(m); reload() }}
              onGo={go} notify={notify} isAdmin={!!me?.is_admin && !asGroup} bar={bar} />
          )}
          {!node && !error && <div className="empty">Loading…</div>}
          <Busy show={nodeBusy} />
        </section>
      </main>

      {dialog?.feature && dialog.feature.render({ node: dialog.node, action: dialog.action, ctx })}
      {dialog && !dialog.feature && <RunDialog node={dialog.node} action={dialog.action} onClose={() => { close(); reload() }} />}
    </div>
  )
}

function seedHierarchy(r, asGroup) {
  const { pages, items, paths } = r
  for (const [uid, page] of Object.entries(pages)) seed(`mh:${asGroup || ''}:${uid}`, page)
  const pageInfo = (p) => ({ total: p.total, offset: p.offset, next: p.next, has_more: p.has_more })
  const empty = { items: [], total: 0, offset: 0, next: 0, has_more: false }
  for (const [uid, cur] of Object.entries(items)) {
    const page = pages[uid] || (cur.child_count === 0 ? empty : null)
    if (!page) continue
    const path = paths[uid] || []
    const parentUid = path[path.length - 1]
    const parentPage = parentUid ? pages[parentUid] : null
    if (parentUid && (!parentPage || !items[parentUid])) continue
    const anc = path.slice(0, -1).map((u) => items[u]).filter(Boolean)
    if (anc.length !== Math.max(0, path.length - 1)) continue
    seed(`tree:${asGroup}:${uid}`, {
      ancestors: anc.map((a) => ({ uid: a.uid, name: a.name, icon: a.icon, cat: a.cat, origin: a.origin })),
      parent: parentUid ? items[parentUid] : null,
      siblings: parentPage ? parentPage.items : [{ ...cur, level: null }],
      siblings_page: parentPage ? pageInfo(parentPage) : { total: 1, offset: 0, next: 1, has_more: false },
      current: uid, children: page.items, children_page: pageInfo(page), current_item: cur,
    })
  }
}
