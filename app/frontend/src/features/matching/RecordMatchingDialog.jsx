import { useEffect, useMemo, useState } from 'react'
import { Dialog, useBusy } from '../../components/Dialogs.jsx'
import { BY_BADGE, Details, STATUS_BADGE, fmt } from './shared.jsx'
import { matchingApi } from './api.js'
import { HELP } from '../../core/docs.js'

// Matching of one source record or golden record: its entity, its pairs, and steward fixes.
function Picker({ ctx, onPick, placeholder }) {
  const [q, setQ] = useState('')
  const [hits, setHits] = useState([])
  useEffect(() => { const t = setTimeout(() => matchingApi.matchCandidates(ctx, q).then(setHits).catch(() => setHits([])), 220); return () => clearTimeout(t) }, [q, ctx])
  return (
    <div className="stack" style={{ gap: 6 }}>
      <input className="in" autoFocus placeholder={placeholder} value={q} onChange={(e) => setQ(e.target.value)} aria-label={placeholder} />
      <div className="box" style={{ maxHeight: 220 }}>{hits.map((h) => (
        <button key={h.uid} className="lnk" style={{ display: 'flex', width: '100%', textAlign: 'left', padding: '6px 10px', gap: 8, borderBottom: '1px solid var(--line-2)' }} onClick={() => onPick(h)}>
          <span style={{ flex: 1 }}>{h.label}</span><span className="faint" style={{ fontSize: 12 }}>{h.sub}</span>{h.golden && <span className="mono faint" style={{ fontSize: 11 }}>{h.golden}</span>}</button>))}
        {!hits.length && <div className="faint" style={{ padding: 10, fontSize: 12 }}>No match.</div>}</div>
    </div>
  )
}

export default function RecordMatchingDialog({ node, onClose, onGo }) {
  const [m, setM] = useState(null)
  const [mode, setMode] = useState(null)   // same | merge | details
  const [other, setOther] = useState(null)
  const [comment, setComment] = useState('')
  const [msg, setMsg] = useState(null)
  const [busy, err, run, setErr] = useBusy()
  const golden = node.origin === 'generated'
  const load = () => matchingApi.matchRecord(node.uid).then(setM).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [node.uid])  // eslint-disable-line react-hooks/exhaustive-deps
  const others = useMemo(() => (m?.members || []).filter((x) => x.uid !== node.uid), [m, node.uid])
  const act = (fn, text) => run(async () => { await fn(); setMsg(text); setMode(null); setOther(null); setComment(''); await load() })
  return (
    <Dialog title={golden ? 'Matching · golden record' : 'Matching · source record'} help={HELP.record_matching} size="md" labelledBy="rmD" onClose={onClose} busy={busy || (!m && !err)} busyLabel="Working…"
      sub={<>{node.name} <span className="mono">{node.uid}</span>{m?.rule ? <> · Match Rule <strong style={{ color: 'var(--ink)' }}>{m.rule.name}</strong> ({m.rule.method})</> : null}</>}
      footer={<><span style={{ fontSize: 12, color: 'var(--ink-3)' }}>Decisions are Match Pairs with Status <em>Same entity</em> / <em>Not the same</em>; they take effect in the next Run Matching and are kept forever.</span>
        <div className="r"><button className="btn" onClick={onClose}>Close</button></div></>}>
      <div className="dbody mbody" style={{ gap: 12 }}>
        {err && <div className="error">{err}</div>}
        {msg && <div className="runbar ok">{msg}</div>}
        {m && !m.rule && <div className="note">This record’s table is not a source of any Match Rule.</div>}
        {m?.rule && <>
          {!golden && <div className="grid2" style={{ gap: 8, fontSize: 13 }}>
            <div>Golden UID: {m.golden ? <button className="lnk mono" onClick={() => onGo && onGo(m.golden)}>{m.golden}</button> : <span className="faint">not matched yet</span>}</div>
            <div>Match Score: <span className="mono">{fmt(m.score)}</span> <span className={`badge ${BY_BADGE(m.by)}`}>{m.by || '—'}</span></div>
          </div>}
          <div className="row" style={{ gap: 8 }}>
            {!golden && <button className="btn" disabled={!others.length} onClick={() => setMode('split')}>Not this entity…</button>}
            {!golden && <button className="btn" onClick={() => setMode('same')}>Same entity as…</button>}
            {golden && <button className="btn" onClick={() => setMode('merge')}>Merge with…</button>}
            <button className="btn" onClick={() => setMode('details')}>Match details</button>
          </div>
          {mode === 'split' && <div className="stack" style={{ border: '1px solid var(--line)', borderRadius: 8, padding: 12 }}>
            <div style={{ fontSize: 13 }}>Saves <em>Not the same</em> between this record and the {others.length} other record(s) of its entity. The next run moves it to its own entity (or another one it matches).</div>
            <input className="in" placeholder="Comment (optional)" value={comment} onChange={(e) => setComment(e.target.value)} aria-label="Comment" />
            <div className="row" style={{ gap: 8 }}><button className="btn dng" onClick={() => act(() => matchingApi.matchSplit(node.uid, { comment }), `Saved ${others.length} “Not the same” decision(s)`)}>Not this entity</button><button className="btn" onClick={() => setMode(null)}>Cancel</button></div>
          </div>}
          {(mode === 'same' || mode === 'merge') && <div className="stack" style={{ border: '1px solid var(--line)', borderRadius: 8, padding: 12 }}>
            {!other ? <Picker ctx={node.uid} onPick={setOther} placeholder={mode === 'merge' ? 'Search the golden records…' : 'Search the source records…'} />
              : <><div style={{ fontSize: 13 }}>{mode === 'merge' ? 'Merge' : 'Same entity as'} <strong>{other.label}</strong>{other.sub ? ` · ${other.sub}` : ''}</div>
                <input className="in" placeholder="Comment (optional)" value={comment} onChange={(e) => setComment(e.target.value)} aria-label="Comment" />
                <div className="row" style={{ gap: 8 }}>
                  <button className="btn pri" onClick={() => act(() => (mode === 'merge' ? matchingApi.matchMerge(node.uid, { other: other.uid, comment }) : matchingApi.matchDecide(node.uid, { a: node.uid, b: other.uid, status: 'same', comment })), 'Saved “Same entity” — applied in the next Run Matching')}>Save decision</button>
                  <button className="btn" onClick={() => setOther(null)}>Pick another</button></div></>}
          </div>}
          {mode === 'details' && <div className="stack" style={{ border: '1px solid var(--line)', borderRadius: 8, padding: 12 }}>
            {!golden && !other && <><div style={{ fontSize: 12, color: 'var(--ink-3)' }}>Compare with a member of the entity or any other record:</div>
              <div className="row" style={{ gap: 6 }}>{others.map((x) => <button key={x.uid} className="chip src" onClick={() => setOther({ uid: x.uid, label: x.label })}>{x.label}</button>)}</div>
              <Picker ctx={node.uid} onPick={setOther} placeholder="Search the source records…" /></>}
            {golden && !other && <div className="row" style={{ gap: 6 }}>{m.members.slice(1).map((x) => <button key={x.uid} className="chip src" onClick={() => setOther({ uid: x.uid, label: x.label, a: m.members[0].uid })}>{m.members[0].label} ↔ {x.label}</button>)}
              {m.members.length < 2 && <span className="faint">One source record — nothing to compare.</span>}</div>}
            {other && <><div className="row" style={{ justifyContent: 'space-between' }}><span style={{ fontSize: 13 }}>{golden ? '' : 'This record ↔ '}{other.label}</span><button className="lnk" style={{ fontSize: 12 }} onClick={() => setOther(null)}>Compare another</button></div>
              <Details ctx={node.uid} a={other.a || node.uid} b={other.uid} /></>}
          </div>}
          <h3 className="sec">{golden ? `Source records · ${m.members.length} with Golden UID ${node.uid}` : `Entity · ${m.members.length} record(s)`}</h3>
          <div className="box"><table className="pv"><thead><tr><th>Source record</th><th>Score</th><th>Matched by</th>{golden && <th />}</tr></thead>
            <tbody>{m.members.map((x) => <tr key={x.uid} style={x.uid === node.uid ? { background: 'var(--accent-bg-2)' } : undefined}>
              <td><div>{x.name}</div><div className="mono faint" style={{ fontSize: 11 }}>{x.label}</div></td><td className="mono">{fmt(x.score)}</td>
              <td><span className={`badge ${BY_BADGE(x.by)}`}>{x.by || '—'}</span></td>
              {golden && <td><button className="btn" style={{ height: 26, fontSize: 12 }} disabled={m.members.length < 2} onClick={() => act(() => matchingApi.matchSplit(node.uid, { record: x.uid }), `${x.label} will be split off in the next run`)}>Split off</button></td>}</tr>)}</tbody></table></div>
          {!golden && m.pairs.length > 0 && <><h3 className="sec">Match Pairs of this record</h3>
            <div className="box"><table className="pv"><thead><tr><th>Other record</th><th>Status</th><th>Score</th><th>By</th><th>Comment</th></tr></thead>
              <tbody>{m.pairs.map((x) => <tr key={x.uid}><td>{x.other_label}</td><td><span className={`badge ${STATUS_BADGE[x.status]}`}>{x.status}</span></td><td className="mono">{fmt(x.score)}</td><td>{x.by || '—'}</td><td>{x.comment || '—'}</td></tr>)}</tbody></table></div></>}
        </>}
      </div>
    </Dialog>
  )
}
