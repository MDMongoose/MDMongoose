import { useEffect, useState } from 'react'
import { Dialog, useBusy } from '../../components/Dialogs.jsx'
import { Tabs } from '../../components/Tabs.jsx'
import { Details, STATUS_BADGE, fmt, short } from './shared.jsx'
import { matchingApi } from './api.js'
import { HELP } from '../../core/docs.js'

// Review matches: the open pairs of a Match Rule, decided Same entity or Not the same by a steward.
export default function ReviewDialog({ node, onClose }) {
  const [q, setQ] = useState(null)
  const [tab, setTab] = useState('open')
  const [cur, setCur] = useState(null)
  const [comment, setComment] = useState('')
  const [done, setDone] = useState({})
  const [busy, err, run, setErr] = useBusy()
  const load = () => matchingApi.matchReview(node.uid).then((x) => { setQ(x); setCur((c) => c || x.open[0]?.uid || null) }).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [node.uid])  // eslint-disable-line react-hooks/exhaustive-deps
  const list = q ? (tab === 'open' ? q.open : q.decided) : []
  const p = list.find((x) => x.uid === cur) || list[0]
  const decide = (status) => run(async () => {
    if (status !== 'skip') await matchingApi.matchDecide(node.uid, { pair: p.uid, status, comment })
    setDone({ ...done, [p.uid]: status }); setComment('')
    const next = list.find((x) => x.uid !== p.uid && !done[x.uid])
    setCur(next?.uid || p.uid)
  })
  return (
    <Dialog title="Review matches" help={HELP.review_matches} size="full" labelledBy="rvT" onClose={onClose} busy={busy || !q} busyLabel="Working…"
      sub={q && <>Match Rule <strong style={{ color: 'var(--ink)' }}>{q.rule.name}</strong> · {q.rule.method} · pairs scored between review ({fmt(q.rule.review)}) and link ({fmt(q.rule.link)}) in the last run, one per pair of entities · deciding needs Modifier on {node.name}</>}
      footer={<>
        <span style={{ fontSize: 12, color: 'var(--ink-3)' }}>{Object.values(done).filter((x) => x !== 'skip').length} decided in this session · decisions take effect in the next Run Matching (incremental: only the affected entities). Many pairs at once: Edit Children on the rule’s Pairs folder.</span>
        <div className="r"><button className="btn" onClick={onClose}>Close</button></div>
      </>}>
      <Tabs tabs={[['open', `Open (${q?.open_count ?? '…'})`], ['decided', `Decided (${q?.decided_count ?? '…'})`]]} cur={tab} onPick={(t) => { setTab(t); setCur(null) }} />
      {err && <div className="error" style={{ margin: '10px 20px 0' }}>{err}</div>}
      <div style={{ flex: '1 1 auto', minHeight: 0, display: 'flex' }}>
        <nav aria-label="Pairs" style={{ flex: '0 0 320px', borderRight: '1px solid var(--line-2)', overflow: 'auto', padding: 10, display: 'flex', flexDirection: 'column', gap: 6 }}>
          {list.map((x) => {
            const on = p && x.uid === p.uid, st = done[x.uid]
            return <button key={x.uid} onClick={() => setCur(x.uid)} aria-current={on} style={{ textAlign: 'left', padding: '8px 10px', borderRadius: 8, cursor: 'pointer', color: 'var(--ink)', background: on ? 'var(--accent-bg-2)' : '#fff', border: on ? '2px solid var(--accent)' : '1px solid var(--line-2)' }}>
              <span className="row" style={{ justifyContent: 'space-between', flexWrap: 'nowrap' }}><span style={{ fontSize: 13, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{x.a.name}</span><span className="mono" style={{ fontSize: 12 }}>{fmt(x.score)}</span></span>
              <span style={{ display: 'block', fontSize: 12, color: 'var(--ink-2)' }}>↔ {x.b.name}</span>
              <span className={`badge ${STATUS_BADGE[st === 'same' ? 'Same entity' : st === 'not' ? 'Not the same' : x.status] || 'b-grey'}`} style={{ marginTop: 4 }}>{st === 'same' ? 'Same entity' : st === 'not' ? 'Not the same' : st === 'skip' ? 'Skipped' : x.status}</span>
            </button>
          })}
          {q && !list.length && <div className="empty">{tab === 'open' ? 'Nothing to review.' : 'No decisions yet.'}</div>}
        </nav>
        <section aria-label="Pair" style={{ flex: '1 1 auto', minWidth: 0, overflow: 'auto', padding: '14px 20px', display: 'flex', flexDirection: 'column', gap: 12 }}>
          {p && <>
            <div className="row" style={{ justifyContent: 'space-between' }}><h2 style={{ margin: 0, fontSize: 16 }}>Same entity?</h2>
              <span className="faint" style={{ fontSize: 12 }}>A is now in <span className="mono">{p.a.golden || '—'}</span> · B is now in <span className="mono">{p.b.golden || '—'}</span>{p.reason ? ` · ${p.reason}` : ''}</span></div>
            <Details ctx={node.uid} a={p.a.uid} b={p.b.uid} />
            {tab === 'open' && q.can_decide && <>
              <div><label className="lbl" htmlFor="cmt">Comment <span className="hint">— optional</span></label><input id="cmt" className="in" value={comment} onChange={(e) => setComment(e.target.value)} /></div>
              <div className="row" style={{ gap: 8 }}>
                <button className="btn pri" onClick={() => decide('same')}>Same entity</button>
                <button className="btn dng" onClick={() => decide('not')}>Not the same</button>
                <button className="btn" onClick={() => decide('skip')}>Skip</button>
                <span style={{ fontSize: 12, color: 'var(--ink-3)' }}>Same entity → B’s entity joins A’s in the next run · Not the same → never linked, even by a future score</span>
              </div></>}
            {tab === 'decided' && <div className="result">{p.status} · {p.by} · {short(p.at)}{p.comment ? ` · “${p.comment}”` : ''} <button className="lnk" style={{ marginLeft: 'auto' }} onClick={() => run(async () => { await matchingApi.matchDecide(node.uid, { pair: p.uid, status: 'open' }); await load() })}>Reopen</button></div>}
          </>}
        </section>
      </div>
    </Dialog>
  )
}

