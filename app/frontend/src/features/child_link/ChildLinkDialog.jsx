import { useEffect, useRef, useState } from 'react'
import { api } from '../../core/api.js'
import { Tile } from '../../components/icons.jsx'
import { Dialog, Field, useBusy } from '../../components/Dialogs.jsx'
import { childLinkApi } from './api.js'
import { Select } from '../../components/Select.jsx'
import { HELP } from '../../core/docs.js'

// Add Child Link: pick a node to show under this one, with a link type and position.
export default function LinkDialog({ node, onClose, onDone }) {
  const [types, setTypes] = useState([])
  const [lt, setLt] = useState('')
  const [pos, setPos] = useState('last')
  const [q, setQ] = useState('')
  const [kind, setKind] = useState('')
  const [hits, setHits] = useState([])
  const [sel, setSel] = useState(null)
  const [links, setLinks] = useState(node.child_links || [])
  const [busy, err, run] = useBusy()
  const t = useRef()
  useEffect(() => { api.options('Enum(LinkTypes)').then((o) => { setTypes(o); setLt(o[0]?.uid || '') }).catch(() => {}) }, [])
  useEffect(() => {
    clearTimeout(t.current)
    if (q.trim().length < 2) { setHits([]); return }
    t.current = setTimeout(() => api.search(q.trim(), kind).then(setHits).catch(() => setHits([])), 200)
  }, [q, kind])
  const ltName = types.find((x) => x.uid === lt)?.name || 'link'
  const remove = (uid) => run(async () => { await childLinkApi.unlink(node.uid, uid); setLinks(links.filter((l) => l.uid !== uid)) })
  return (
    <Dialog title="Add Child Link" help={HELP.child_link} onClose={onClose} labelledBy="lnt" busy={busy} busyLabel="Saving…"
      sub={<>Parent <strong style={{ color: 'var(--ink)' }}>{node.name}</strong> <span className="mono">{node.uid}</span>. The linked node keeps its real parent and also appears as a child here.</>}
      footer={<div className="r"><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn pri" disabled={!sel || busy} onClick={() => run(async () => { await childLinkApi.link(node.uid, sel.uid, lt || null, pos); onDone(`${sel.name} is linked under ${node.name}${ltName ? ` as ${ltName}` : ''}`) })}>Add child link</button></div>}>
      <div className="dbody">
        <div className="grid2">
          <Field label="LinkType" ptag="Enum(LinkTypes)" htmlFor="lt">
            <Select id="lt" className="in" value={lt} onChange={(e) => setLt(e.target.value)}>
              {types.map((o) => <option key={o.uid} value={o.uid}>{o.uid} — {o.name}</option>)}
            </Select>
          </Field>
          <Field label="Position among children" htmlFor="ld">
            <Select id="ld" className="in" value={pos} onChange={(e) => setPos(e.target.value)}>
              <option value="last">After existing children</option><option value="first">First</option>
            </Select>
          </Field>
        </div>
        <div className="stack" style={{ gap: 10 }}>
          <h2 className="sec">Target node</h2>
          <div className="row" style={{ flexWrap: 'nowrap' }}>
            <label htmlFor="ls" className="sr">Search target</label>
            <input id="ls" type="search" className="in" placeholder="Search name or UID…" value={q} onChange={(e) => setQ(e.target.value)} autoFocus />
            <Select className="in" aria-label="Filter by type" style={{ width: 180, flex: '0 0 180px' }} value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="">Any type</option><option value="manual">Manual only</option><option value="loaded">Loaded only</option><option value="generated">Golden records</option>
            </Select>
          </div>
          <div role="listbox" aria-label="Search results" className="listbox">
            {hits.length === 0 && <div className="faint" style={{ padding: 12, fontSize: 12 }}>Type at least two characters</div>}
            {hits.map((r) => (
              <button key={r.uid} role="option" aria-selected={sel?.uid === r.uid} className={sel?.uid === r.uid ? 'on' : ''} onClick={() => setSel(r)}>
                <span className={`radio ${sel?.uid === r.uid ? 'on' : ''}`} aria-hidden="true" />
                <Tile name={r.icon} cat={r.cat} loaded={r.origin === 'loaded'} />
                <span style={{ flex: '1 1 auto', minWidth: 0 }}>
                  <span style={{ display: 'block', fontSize: 13, fontWeight: 500 }}>{r.name}</span>
                  <span style={{ display: 'block', fontSize: 12, color: 'var(--ink-3)' }}>{r.path}</span>
                </span>
                <span style={{ fontSize: 11, color: 'var(--ink-2)', border: '1px solid var(--input)', borderRadius: 4, padding: '1px 6px', whiteSpace: 'nowrap' }}>{r.types.join(' + ')}{r.origin !== 'manual' ? ` · ${r.origin}` : ''}</span>
                <span className="mono faint">{r.uid}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="stack">
          <h2 className="sec">Preview</h2>
          <div className="row" style={{ fontSize: 13, padding: '12px 14px', background: 'var(--bg-2)', borderRadius: 6, border: '1px dashed #B5BCC6' }}>
            <span style={{ fontWeight: 500 }}>{node.name}</span>
            <span className="mono" style={{ color: 'var(--accent)' }}>── {ltName} ──▶</span>
            <span style={{ fontWeight: 500 }}>{sel ? sel.name : <span className="faint">choose a target</span>}</span>
          </div>
        </div>
        <div className="stack">
          <h2 className="sec">Existing links on this node</h2>
          {links.length === 0 && <div className="faint" style={{ fontSize: 13 }}>none</div>}
          {links.map((l) => (
            <div key={l.uid} className="row" style={{ justifyContent: 'space-between', fontSize: 13, padding: '8px 12px', border: '1px solid var(--line-2)', borderRadius: 6 }}>
              <span><span className="mono faint">{l.link_type} →</span> {l.name} <span className="mono faint">{l.uid}</span></span>
              <button className="lnk" style={{ color: 'var(--red)', fontSize: 12 }} onClick={() => remove(l.uid)}>Remove</button>
            </div>
          ))}
        </div>
        {err && <div className="error">{err}</div>}
      </div>
    </Dialog>
  )
}
