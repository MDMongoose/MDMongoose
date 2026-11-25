import { useEffect, useState } from 'react'
import { Icon } from '../../components/icons.jsx'
import { survivorshipApi } from '../survivorship/api.js'
import { Select } from '../../components/Select.jsx'

// Step 5: per golden-record property, which source value survives, with a live preview.

const STRATS = [
  ['sp', 'Source Priority', 'The first source in the order with an eligible value wins.'],
  ['mr', 'Most Recent', 'The newest eligible value wins; the order breaks ties.'],
  ['custom', 'Custom', 'A python function picks the winning (source, value) pair.'],
]
const SN = { sp: 'Source Priority', mr: 'Most Recent', custom: 'Custom' }
const BADGE = { sp: 'b-blue', mr: 'b-teal', custom: 'b-orange' }

export default function SurvivorshipStep({ ruleUid, data, cfgs, setCfgs, sources, srcBy, canEdit, body }) {
  const [cur, setCur] = useState(cfgs[0]?.property || null)
  const [previews, setPreviews] = useState({})
  const [record, setRecord] = useState(null)
  const [sim, setSim] = useState(null)
  const sig = JSON.stringify([body, record])
  useEffect(() => {
    if (!cfgs.length) return
    const t = setTimeout(() => {
      cfgs.forEach((c) => survivorshipApi.rulePreview(ruleUid, { ...body, property: c.property, record })
        .then((p) => setPreviews((x) => ({ ...x, [c.property]: p })))
        .catch((e) => setPreviews((x) => ({ ...x, [c.property]: { error: e.message } }))))
    }, 300)
    return () => clearTimeout(t)
  }, [sig])  // eslint-disable-line react-hooks/exhaustive-deps

  const c = cfgs.find((x) => x.property === cur) || cfgs[0]
  if (!c) return <div className="gs-red">No golden record properties yet.</div>
  const setCfg = (patch, prop = c.property) => setCfgs(cfgs.map((x) => (x.property === prop ? { ...x, ...patch } : x)))
  const pv = previews[c.property]
  const mapped = sources.filter((u) => c.mapping[u])
  const order = (c.priority || []).filter((u) => mapped.includes(u)).concat(mapped.filter((u) => !(c.priority || []).includes(u)))
  const moveOrd = (i, d) => { const o = order.slice(); const j = i + d; if (j < 0 || j >= o.length) return; [o[i], o[j]] = [o[j], o[i]]; setCfg({ priority: o }) }
  const recencyMode = (c.recency || 'Loaded At').includes('=') ? 'changed' : 'loaded'
  const recencyMap = Object.fromEntries((c.recency || '').split(';').filter((x) => x.includes('=')).map((x) => x.split('=').map((y) => y.trim())))
  const fn = data.custom_functions.find((f) => f.path === c.custom)
  const copyTo = (to) => {
    if (!to) return
    const targets = to === '*' ? cfgs.filter((x) => x.property !== c.property).map((x) => x.property) : [to]
    setCfgs(cfgs.map((x) => targets.includes(x.property) ? { ...x, strategy: c.strategy, priority: order.slice(), recency: c.recency, custom: c.custom, ignore_empty: c.ignore_empty, ud_wins: c.ud_wins, eligible: c.eligible } : x))
  }

  return (
    <div className="gs-surv">
      <nav aria-label="Golden record properties" className="gs-surv-nav">
        <h2 className="sec" style={{ padding: '0 6px 6px' }}>Golden record property</h2>
        {cfgs.map((x) => {
          const on = x.property === c.property, p = previews[x.property]
          const first = x.strategy === 'custom' ? (x.custom || '').split('.').pop() : x.strategy === 'mr' ? 'newest value wins' : '1. ' + (srcBy[(x.priority || []).find((u) => x.mapping[u])]?.label || '—')
          return (
            <button key={x.property} onClick={() => setCur(x.property)} aria-current={on} className={`mr-item ${on ? 'on' : ''}`}>
              <span className="row" style={{ justifyContent: 'space-between', flexWrap: 'nowrap' }}><span style={{ fontSize: 13, fontWeight: 600 }}>{x.property}</span><span className={`badge ${BADGE[x.strategy]}`}>{SN[x.strategy]}</span></span>
              <span className="mr-item-sub">{first}{p && !p.error ? (p.value != null ? ` → “${p.value}”` : ' → empty') : ''}</span>
            </button>
          )
        })}
      </nav>
      <section aria-label="Property rule" className="gs-surv-main">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <div className="row" style={{ alignItems: 'baseline', gap: 8 }}><h2 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>{c.property}</h2><span className="ptag">{c.ptype}</span></div>
          {canEdit && <div className="row" style={{ gap: 6 }}>
            <label htmlFor="copyto" style={{ fontSize: 12, color: 'var(--ink-3)' }}>Copy these settings to</label>
            <Select id="copyto" className="in sm" style={{ width: 'auto' }} value="" onChange={(e) => copyTo(e.target.value)}>
              <option value="">choose…</option><option value="*">all other properties</option>
              {cfgs.filter((x) => x.property !== c.property).map((x) => <option key={x.property} value={x.property}>{x.property}</option>)}
            </Select></div>}
        </div>
        <div role="radiogroup" aria-label="Strategy" className="grid3" style={{ gap: 8 }}>
          {STRATS.map(([k, l, s]) => <button key={k} role="radio" aria-checked={c.strategy === k} disabled={!canEdit} className={`card-radio ${c.strategy === k ? 'on' : ''}`} onClick={() => setCfg({ strategy: k, custom: k === 'custom' && !c.custom ? (data.custom_functions[0]?.path || '') : c.custom })}><strong>{l}</strong><span className="sub">{s}</span></button>)}
        </div>
        <div className="grid2">
          <div style={{ minWidth: 0 }}>
            <span className="lbl">{c.strategy === 'sp' ? 'Source priority — highest trust first' : c.strategy === 'mr' ? 'Tie-breaker order' : 'Order passed to the function'}</span>
            <ol className="mr-srcs">
              {order.map((u, i) => (
                <li key={u}>
                  <span className="rank">{i + 1}</span><span style={{ flex: '1 1 auto' }}>{srcBy[u]?.label || u}</span>
                  <span className="mono faint" style={{ fontSize: 11 }}>{c.mapping[u]}</span>
                  {canEdit && <><button className="ib" onClick={() => moveOrd(i, -1)} disabled={i === 0} aria-label={`Move ${srcBy[u]?.label} up`}><Icon name="caretUp" size={13} /></button>
                    <button className="ib" onClick={() => moveOrd(i, 1)} disabled={i === order.length - 1} aria-label={`Move ${srcBy[u]?.label} down`}><Icon name="down" size={13} /></button></>}
                </li>
              ))}
              {!order.length && <li className="faint">No source feeds {c.property} — map a column in Structure &amp; sources.</li>}
            </ol>
            {c.strategy === 'mr' && <div style={{ marginTop: 12 }}>
              <label className="lbl" htmlFor="rec">Recency based on</label>
              <Select id="rec" className="in" value={recencyMode} disabled={!canEdit} onChange={(e) => setCfg({ recency: e.target.value === 'loaded' ? 'Loaded At' : order.map((u) => `${u}=`).join('; ') })}>
                <option value="loaded">Record · Loaded At (pipeline)</option><option value="changed">Source column · changed-on (AEDAT / LastModifiedDate)</option></Select>
              {recencyMode === 'changed' && order.map((u) => (
                <div key={u} className="row" style={{ marginTop: 6, flexWrap: 'nowrap' }}><span style={{ fontSize: 12, flex: 1 }}>{srcBy[u]?.label}</span>
                  <Select className="in sm mono" style={{ width: 160 }} value={recencyMap[u] || ''} disabled={!canEdit} aria-label={`Changed-on column of ${srcBy[u]?.label}`} onChange={(e) => setCfg({ recency: order.map((x) => `${x}=${x === u ? e.target.value : (recencyMap[x] || '')}`).join('; ') })}>
                    <option value="">—</option>{(srcBy[u]?.columns || []).map((col) => <option key={col}>{col}</option>)}</Select></div>))}
            </div>}
            {c.strategy === 'custom' && <div style={{ marginTop: 12 }}>
              <label className="lbl" htmlFor="fn">Custom Function <span className="hint">— python handler in mdm_custom</span></label>
              <Select id="fn" className="in mono" value={c.custom} disabled={!canEdit} onChange={(e) => setCfg({ custom: e.target.value })}>
                {!fn && c.custom && <option value={c.custom}>{c.custom}</option>}
                {data.custom_functions.map((f) => <option key={f.path} value={f.path}>{f.path}</option>)}</Select>
              <pre className="code" style={{ marginTop: 8 }}>{fn ? fn.code : `def ${(c.custom || 'fn').split('.').pop()}(candidates: list[tuple[str, Any]]) -> tuple[str, Any]:\n    ...`}</pre>
            </div>}
          </div>
          <div className="gs-box stack" style={{ gap: 8, alignSelf: 'start' }}>
            <span className="sec">Eligibility</span>
            <label className="row" style={{ gap: 8, fontSize: 13 }}><input type="checkbox" checked={c.ignore_empty} disabled={!canEdit} onChange={() => setCfg({ ignore_empty: !c.ignore_empty })} />Ignore empty values</label>
            <label className="row" style={{ gap: 8, fontSize: 13 }}><input type="checkbox" checked={c.ud_wins} disabled={!canEdit} onChange={() => setCfg({ ud_wins: !c.ud_wins })} />UserDefined (manual) value always wins</label>
            <div><label className="lbl" htmlFor="cond" style={{ marginBottom: 4 }}>Eligible if <span className="ptag">Expression(SQL)</span></label>
              <input id="cond" className="in mono" value={c.eligible} disabled={!canEdit} onChange={(e) => setCfg({ eligible: e.target.value })} placeholder="e.g. value RLIKE '^[A-Z]{2}[0-9]+$'" /></div>
            <div className="mr-hint">Golden value: <strong>{c.cleaned ? 'cleaned' : 'original'}</strong> (set in Standardize &amp; clean).</div>
          </div>
        </div>
        <div className="stack">
          <div className="row" style={{ justifyContent: 'space-between' }}>
            <h3 className="sec">Preview{pv?.cluster ? ` · golden record ${pv.cluster.name} (${pv.cluster.records} source records)` : ''}</h3>
            {pv?.cluster?.options?.length > 1 && <Select className="in sm" style={{ width: 'auto' }} value={record || ''} onChange={(e) => setRecord(e.target.value || null)} aria-label="Preview another golden record">
              <option value="">largest entity</option>{pv.cluster.options.map((o) => <option key={o.record} value={o.record}>{o.label}</option>)}</Select>}
          </div>
          {pv?.error && <div className="error">{pv.error}</div>}
          <div className="box"><table className="pv">
            <thead><tr><th>Source</th><th>Record</th><th>Value</th><th>Loaded at</th><th>Eligible</th><th>Result</th></tr></thead>
            <tbody>{(pv?.rows || []).map((r, i) => (
              <tr key={i} style={r.winner ? { background: 'var(--gold-bg)' } : undefined}>
                <td>{r.source_label}</td><td className="mono" style={{ fontSize: 11 }}>{r.record_label}</td>
                <td style={{ fontWeight: r.winner ? 600 : undefined, color: r.value == null ? 'var(--ink-4)' : undefined, fontStyle: r.value == null ? 'italic' : undefined }}>{r.value ?? '(empty)'}</td>
                <td className="mono">{(r.loaded_at || '—').slice(0, 16).replace('T', ' ')}</td>
                <td style={{ color: r.eligible ? 'var(--green)' : 'var(--red)' }}>{r.eligible ? 'yes' : 'no — ' + r.why}</td><td style={{ color: 'var(--ink-2)' }}>{r.step}</td>
              </tr>))}
              {pv && !pv.error && !(pv.rows || []).length && <tr><td colSpan={6} className="faint">{pv.reason}</td></tr>}
              {!pv && <tr><td colSpan={6} className="faint">Loading…</td></tr>}
            </tbody></table></div>
          {pv && !pv.error && <div className={`result ${pv.value != null ? 'win' : ''}`}><Icon name="star2" size={16} />
            <span>{pv.value != null ? `Golden value: “${pv.value}” from ${pv.source} — ` : 'Golden value: empty — '}{pv.reason}</span></div>}
          <div className="row" style={{ gap: 10 }}>
            <button className="btn" style={{ minHeight: 30 }} onClick={() => survivorshipApi.ruleSimulate(ruleUid, { ...body, limit: 100 }).then(setSim).catch((e) => setSim({ error: e.message }))}>Simulate on 100 records</button>
            {sim?.error && <span className="error">{sim.error}</span>}
          </div>
          {sim && !sim.error && <div className="box"><table className="pv"><thead><tr><th>Property</th><th>Winning source (count) · {sim.records} records → {sim.golden} golden records</th><th>Empty</th></tr></thead>
            <tbody>{sim.properties.map((p) => <tr key={p.property}><td>{p.property}</td><td>{Object.entries(p.wins).map(([s, n]) => `${s}: ${n}`).join(' · ') || '—'}</td><td className="mono">{p.empty}</td></tr>)}</tbody></table></div>}
        </div>
      </section>
    </div>
  )
}
