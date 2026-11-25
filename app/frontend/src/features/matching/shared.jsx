import { useEffect, useState } from 'react'
import { matchingApi } from './api.js'

// Badges, formatting and panels used by several matching dialogs.

export const ROLE_BADGE = { exact: 'b-green', fuzzy: 'b-orange', splink: 'b-blue', blocking: 'b-grey' }
export const STATUS_BADGE = { 'Same entity': 'b-green', 'Not the same': 'b-red', Open: 'b-grey' }
export const ACTION_BADGE = { 'New entity': 'b-green', 'Split off': 'b-green', Merged: 'b-violet', 'Record joined': 'b-orange',
  'Record left': 'b-orange', Changed: 'b-orange', Retired: 'b-red' }
export const BY_BADGE = (by) => (by || '').startsWith('Exact') ? 'b-green' : by === 'Steward' ? 'b-violet' : by === 'Alone' || !by ? 'b-grey' : 'b-blue'
export const fmt = (v, d = 2) => (v == null || v === '' ? '—' : Number(v).toFixed(d))
export const short = (t) => (t || '').replace('T', ' ').slice(0, 16)


export function Weight({ w }) {
  if (w == null) return <span className="faint mono" style={{ fontSize: 11 }}>not trained</span>
  const pct = Math.min(50, Math.abs(w) / 12 * 50)
  return (
    <span className="row" style={{ gap: 8, flexWrap: 'nowrap' }}>
      <span style={{ position: 'relative', width: 140, height: 8, background: 'var(--bg-3)', borderRadius: 2 }} aria-hidden="true">
        <span style={{ position: 'absolute', top: 0, bottom: 0, borderRadius: 2, ...(w >= 0 ? { left: '50%', background: 'var(--green-dot)' } : { right: '50%', background: '#C8553D' }), width: `${pct}%` }} />
      </span>
      <span className="mono" style={{ fontSize: 12, width: 44, textAlign: 'right' }}>{w > 0 ? '+' : ''}{w.toFixed(1)}</span>
    </span>
  )
}


// Two records side by side with the evidence per field.
export function Details({ ctx, a, b }) {
  const [d, setD] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => { setD(null); setErr(null); if (a && b) matchingApi.matchDetails(ctx, a, b).then(setD).catch((e) => setErr(e.message)) }, [ctx, a, b])
  if (err) return <div className="error">{err}</div>
  if (!d) return <div className="empty">Loading…</div>
  const e = d.explanation || {}
  return (
    <div className="stack">
      <div className="row" style={{ gap: 10 }}><span className={`badge ${e.band === 'link' || d.exact_keys.length ? 'b-green' : e.band === 'review' ? 'b-orange' : 'b-grey'}`}>{d.verdict}</span>
        {d.same_entity_now && <span className="badge b-blue">one entity now</span>}
        {d.decision && <span className={`badge ${STATUS_BADGE[d.decision.status]}`}>decision: {d.decision.status}{d.decision.by ? ` · ${d.decision.by}` : ''}</span>}</div>
      <div className="box"><table className="pv">
        <thead><tr><th>Match field</th><th>A · {d.a.label}</th><th>B · {d.b.label}</th><th>Cleaned</th>{e.method === 'splink' && <th>Level · weight</th>}</tr></thead>
        <tbody>{d.fields.map((f) => {
          const c = (e.comparisons || []).find((x) => x.field === f.field)
          return <tr key={f.field}><td><span className={`badge ${ROLE_BADGE[f.role]}`} style={{ marginRight: 6 }}>{f.role === 'exact' ? 'key' : f.role}</span>{f.field}</td>
            <td>{f.a ?? <span className="faint">—</span>}</td><td>{f.b ?? <span className="faint">—</span>}</td>
            <td className="mono" style={{ fontSize: 11, color: f.equal ? 'var(--green)' : 'var(--ink-3)' }}>{f.a_clean ?? '∅'} {f.equal ? '=' : '·'} {f.b_clean ?? '∅'}</td>
            {e.method === 'splink' && <td>{c ? <span className="row" style={{ gap: 6, flexWrap: 'nowrap' }}><span style={{ fontSize: 12, width: 130 }}>{c.level}</span><Weight w={c.weight} /></span> : <span className="faint">—</span>}</td>}</tr>
        })}
          {e.method === 'splink' && <tr><td colSpan={4} style={{ textAlign: 'right' }}>Prior + weights → probability <strong className="mono">{fmt(e.score, 3)}</strong></td><td><Weight w={e.weight} /></td></tr>}</tbody>
      </table></div>
      {e.method === 'fuzzy' && <div className="stack" style={{ gap: 6 }}>{e.rules.map((r) => (
        <div key={r.name} style={{ fontSize: 12.5, border: '1px solid var(--line)', borderRadius: 6, padding: '6px 10px', background: r.status === 'link' ? '#F6FBF7' : r.status === 'review' ? '#FFFBF6' : '#fff' }}>
          <strong>{r.name}</strong> <span className={`badge ${r.status === 'link' ? 'b-green' : r.status === 'review' ? 'b-orange' : 'b-grey'}`}>{r.status === 'no' ? 'no' : r.status}</span>
          <span style={{ color: 'var(--ink-2)' }}> — {r.conditions.map((c) => `${c.field} ${c.similarity == null ? 'empty' : c.similarity.toFixed(2)} (${c.result})`).join(' · ')}</span></div>))}</div>}
    </div>
  )
}

