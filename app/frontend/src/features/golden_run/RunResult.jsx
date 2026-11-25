import { ACTION_BADGE, fmt } from '../matching/shared.jsx'

// What a matching run changed (or would change): counts and the changed entities, from the run log.
export default function RunResult({ log, title }) {
  const m = (log || []).find((r) => r.activity === 'match')
  const g = (log || []).find((r) => r.activity === 'golden')
  if (!m) return null
  const s = m.stats || {}
  const tiles = [[s.changed, 'records re-matched'], [s.pairs_scored, 'pairs scored'], [s.new_entities, 'new entities'],
    [s.changed_entities, 'entities changed'], [`${s.merged ?? 0} · ${s.split ?? 0}`, 'merged · split'], [s.review_band, 'pairs to review']]
  return (
    <div className="stack">
      <div className="row" style={{ justifyContent: 'space-between' }}><h2 className="sec">{title || (s.preview ? 'Preview — what matching would change' : 'Matching — result')}</h2>
        <span className="faint" style={{ fontSize: 12 }}>{s.mode} · {s.records} records{s.filtered_out ? ` (${s.filtered_out} left out by the record filter)` : ''} · {s.affected} in the affected entities · {fmt(s.seconds, 1)} s{s.model ? ` · model ${s.model}` : ''}</span></div>
      <div className="gs-tiles">
        {tiles.map(([n, l]) => <div key={l}><strong>{(n ?? 0).toLocaleString?.('en') ?? n}</strong><span>{l}</span></div>)}
      </div>
      <div className="box"><table className="pv gs-table">
        <thead><tr><th>Entity · Golden UID</th><th>Change</th><th>Source records after the run</th><th>Score</th><th>Why</th></tr></thead>
        <tbody>{(s.rows || []).map((r) => (
          <tr key={r.uid + r.action}><td><div style={{ fontWeight: 600 }}>{r.name}</div><div className="mono faint" style={{ fontSize: 11 }}>{r.new ? `new · ${r.uid}` : r.uid}</div></td>
            <td><span className={`badge ${ACTION_BADGE[r.action] || 'b-grey'}`}>{r.action}</span></td>
            <td><div className="row" style={{ gap: 4 }}>{r.records.map((x) => <span key={x.uid} className="chip src" style={x.moved ? { borderColor: 'var(--green-line)', background: '#F6FBF7' } : undefined} title={x.name}>{x.label}</span>)}</div></td>
            <td className="mono">{fmt(r.score)}</td><td style={{ color: 'var(--ink-2)' }}>{r.why}</td></tr>))}
          {!(s.rows || []).length && <tr><td colSpan={5} className="faint">No entity changed.</td></tr>}</tbody>
      </table></div>
      {g && <div className="result">Survivorship: {g.stats?.created ?? 0} created · {g.stats?.changed ?? 0} changed · {g.stats?.retired ?? 0} retired golden records{g.stats?.unmatched ? ` · ${g.stats.unmatched} records not matched yet` : ''}</div>}
      {m.status === 'FAILED' && <div className="error">{m.message}</div>}
    </div>
  )
}
