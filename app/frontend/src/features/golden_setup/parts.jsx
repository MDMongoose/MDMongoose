import { useEffect, useState } from 'react'
import { Icon } from '../../components/icons.jsx'
import { RunStatus } from '../../components/Dialogs.jsx'
import { ROLE_BADGE, Weight, fmt, short } from '../matching/shared.jsx'
import { matchingApi } from '../matching/api.js'
import { Select } from '../../components/Select.jsx'

// Pieces of the Golden record setup steps: cleaning chains, blocking, fuzzy rules, the Splink model, a test pair.

export const CMP_HELP = {
  Exact: 'equal or not', Levenshtein: 'edit distance: typos, missing or extra letters',
  'Damerau-Levenshtein': 'edit distance, a swap of two letters counts as one edit',
  'Jaro-Winkler': 'typos, a shared start counts more (names)', 'Jaro-Winkler order ignored': 'Jaro-Winkler, word order ignored (Doe John = John Doe)',
  Jaccard: 'shared words / all words', Soundex: 'sounds alike in English (Robert = Rupert); equal or not',
}
export const usesField = (text, name) => !!name && (text || '').split(/\s*&\s*|\s+AND\s+/i).some((p) => p.replace(/\[.*\]\s*$/, '').trim().toLowerCase() === name.toLowerCase())

// "Country & Legal Name[:3]" -> "Same Country and same first 3 characters of Legal Name"
export function blockLabel(text) {
  const parts = (text || '').split(/\s*&\s*|\s+AND\s+/i).filter((x) => x.trim())
  const out = parts.map((raw) => {
    const m = raw.trim().match(/^(.+?)\s*(?:\[(-?\d*):(-?\d*)\])?$/)
    if (!m) return raw
    const [, f, a, b] = m
    if (a && a.startsWith('-') && !b) return `same last ${a.slice(1)} characters of ${f}`
    if (!a && b) return `same first ${b} characters of ${f}`
    return `same ${f}`
  })
  const s2 = out.join(' and ')
  return s2 ? s2[0].toUpperCase() + s2.slice(1) : ''
}

// ------------------------------------------------------------------ a cleaning chain
const stdOf = (s) => (/^Standardize\s+(\S+)/i.exec(s) || [])[1]
const wordsOf = (s) => (/^Drop\s+words\s+(\S+)/i.exec(s) || [])[1]
const quote = (v) => `'${(v || '').replace(/'/g, "''")}'`

// "Standardize · Countries", "Drop words · LegalForms": what a step shows; its tooltip says what it does.
function stepLabel(s, tables, lists) {
  const u = stdOf(s), w = wordsOf(s)
  if (u) return `Standardize · ${tables.find((t) => t.uid === u)?.name || u}`
  if (w) return `Drop words · ${lists.find((t) => t.uid === w)?.name || w}`
  return s
}
function stepHelp(s, tables, lists, help) {
  const u = stdOf(s), w = wordsOf(s)
  if (w) {
    const l = lists.find((x) => x.uid === w)
    return l ? `removes the values of the Enum ${l.name} as whole words: ${l.words.join(', ')}${l.words.length >= 12 ? ' …' : ''}` : 'no such Enum'
  }
  if (!u) return help[s.split(' ')[0]] || help[s] || ''
  const t = tables.find((x) => x.uid === u)
  if (!t) return 'no such Standardization Table'
  return `${t.columns.join(', ')} → ${t.key}; no match → ${t.default != null ? `'${t.default}'` : 'the source value'}`
}

export function Cleaning({ steps, options, tables = [], lists = [], disabled, onChange, label }) {
  const [rx, setRx] = useState(null)
  const [std, setStd] = useState(false)
  const [words, setWords] = useState(false)
  const help = Object.fromEntries(options.map((o) => [o.name, o.help]))
  const move = (i, dir) => { const s = [...steps]; const j = i + dir; if (j < 0 || j >= s.length) return; [s[i], s[j]] = [s[j], s[i]]; onChange(s) }
  return (
    <div className="gs-pipe">
      {steps.map((s, i) => <span key={i} className="row" style={{ gap: 4, flexWrap: 'nowrap' }}>
        {i > 0 && <span className="faint" aria-hidden="true">→</span>}
        <span className={`chip mr-step ${stdOf(s) || wordsOf(s) ? 'std' : ''}`} title={stepHelp(s, tables, lists, help)}>
          {!disabled && i > 0 && <button className="ib ghost" style={{ width: 16, height: 16 }} onClick={() => move(i, -1)} aria-label={`Move ${s} earlier`}>‹</button>}
          {stepLabel(s, tables, lists)}
          {!disabled && <button className="ib ghost" style={{ width: 16, height: 16 }} onClick={() => onChange(steps.filter((_, j) => j !== i))} aria-label={`Remove ${stepLabel(s, tables, lists)}`}>×</button>}</span>
      </span>)}
      {!disabled && <Select className="in sm dashed" value="" aria-label={`Add a cleaning step to ${label}`} onChange={(e) => {
        const v = e.target.value
        if (!v) return
        if (v === 'Regex Replace') setRx({ re: '[^A-Z0-9]', to: '' })
        else if (v === 'Standardize') setStd(true)
        else if (v === 'Drop words') setWords(true)
        else onChange([...steps, v])
      }}><option value="">+ step</option>{options.map((o) => <option key={o.name} value={o.name} title={o.help}>{o.name} — {o.help}</option>)}</Select>}
      {std && <Select className="in sm" autoFocus value="" aria-label="Standardization Table" onChange={(e) => { if (e.target.value) onChange([...steps, `Standardize ${e.target.value}`]); setStd(false) }}>
        <option value="">Standardization Table…</option>
        {tables.map((t) => <option key={t.uid} value={t.uid}>{t.name} — {t.columns.join(', ')} → {t.key}</option>)}
        {!tables.length && <option value="" disabled>none yet — add the NodeType Standardization Table to a node</option>}</Select>}
      {words && <Select className="in sm" autoFocus value="" aria-label="Enum of the words to drop" onChange={(e) => { if (e.target.value) onChange([...steps, `Drop words ${e.target.value}`]); setWords(false) }}>
        <option value="">Enum of the words to drop…</option>
        {lists.map((t) => <option key={t.uid} value={t.uid}>{t.name} — {t.words.slice(0, 6).join(', ')}{t.words.length > 6 ? ' …' : ''}</option>)}</Select>}
      {rx && <span className="row" style={{ gap: 4 }}>
        <input className="in sm mono" style={{ width: 150 }} value={rx.re} placeholder="regex" onChange={(e) => setRx({ ...rx, re: e.target.value })} aria-label="Regular expression" />
        <span className="faint">→</span>
        <input className="in sm mono" style={{ width: 90 }} value={rx.to} placeholder="replace with" onChange={(e) => setRx({ ...rx, to: e.target.value })} aria-label="Replace with" />
        <button className="btn" style={{ minHeight: 28 }} disabled={!rx.re} onClick={() => { onChange([...steps, `Regex Replace ${quote(rx.re)} ${quote(rx.to)}`]); setRx(null) }}>Add</button>
        <button className="ib ghost" onClick={() => setRx(null)} aria-label="Cancel">×</button></span>}
    </div>
  )
}

// ------------------------------------------------------------------ blocking rules with their pair counts
export function Blocking({ blocking, setBlocking, fieldNames, counts, canEdit }) {
  const [f1, setF1] = useState(fieldNames[0] || '')
  const [p1, setP1] = useState('')
  const [f2, setF2] = useState('')
  const [p2, setP2] = useState('')
  const part = (f, p) => (p === 'first' ? `${f}[:3]` : p === 'last' ? `${f}[-7:]` : f)
  const add = () => { if (!f1) return; setBlocking([...blocking, f2 ? `${part(f1, p1)} & ${part(f2, p2)}` : part(f1, p1)]) }
  const max = Math.max(1, ...((counts?.rules || []).map((r) => r.pairs || 0)))
  return <>
    <ol className="mr-blocks">
      {blocking.map((b, i) => {
        const n = counts?.rules?.find((r) => r.rule === b)?.pairs
        return <li key={i}>
          <span className="rank">{i + 1}</span>
          <div style={{ flex: '1 1 auto', minWidth: 0 }}>
            <div style={{ fontSize: 13.5, fontWeight: 500 }}>{blockLabel(b) || <span className="faint">empty rule</span>}</div>
            <input className="in sm mono" style={{ marginTop: 4, maxWidth: 420 }} value={b} disabled={!canEdit} onChange={(e) => setBlocking(blocking.map((x, j) => (j === i ? e.target.value : x)))} aria-label={`Blocking rule ${i + 1}`} />
          </div>
          <div style={{ width: 200 }}>
            <div className="mono" style={{ fontSize: 12, textAlign: 'right' }}>{n == null ? '…' : n.toLocaleString('en')} pairs</div>
            <div className="mr-meter"><span style={{ width: `${n ? Math.max(2, n / max * 100) : 0}%` }} /></div>
          </div>
          {canEdit && <button className="ib ghost" onClick={() => setBlocking(blocking.filter((_, j) => j !== i))} aria-label={`Remove blocking rule ${i + 1}`}><Icon name="close" size={12} /></button>}
        </li>
      })}
      {!blocking.length && <li className="faint" style={{ fontSize: 13 }}>No blocking rules: every record is compared with every other (only fine for small sources).</li>}
    </ol>
    {canEdit && <div className="mr-builder">
      <div><label className="lbl" htmlFor="bf1">Same</label><Select id="bf1" className="in sm" value={f1} onChange={(e) => setF1(e.target.value)}>{fieldNames.map((n) => <option key={n}>{n}</option>)}</Select></div>
      <div><label className="lbl" htmlFor="bp1">Part</label><Select id="bp1" className="in sm" value={p1} onChange={(e) => setP1(e.target.value)}><option value="">whole value</option><option value="first">first 3 characters</option><option value="last">last 7 characters</option></Select></div>
      <div><label className="lbl" htmlFor="bf2">and same (optional)</label><Select id="bf2" className="in sm" value={f2} onChange={(e) => setF2(e.target.value)}><option value="">—</option>{fieldNames.filter((n) => n !== f1).map((n) => <option key={n}>{n}</option>)}</Select></div>
      {f2 && <div><label className="lbl" htmlFor="bp2">Part</label><Select id="bp2" className="in sm" value={p2} onChange={(e) => setP2(e.target.value)}><option value="">whole value</option><option value="first">first 3 characters</option><option value="last">last 7 characters</option></Select></div>}
      <button className="btn" onClick={add} disabled={!f1}><Icon name="plus" size={13} />Add blocking rule</button>
    </div>}
    {counts?.error && <div className="error">{counts.error}</div>}
    {counts && !counts.error && <div className="mr-total">
      <span>Pairs to compare in a full run: <strong>{counts.total == null ? '—' : counts.total.toLocaleString('en')}</strong> of {counts.all_pairs.toLocaleString('en')} possible between {counts.records.toLocaleString('en')} records</span>
      <span className="faint" style={{ fontSize: 12 }}>counted from the current data, after the record filter</span></div>}
  </>
}

// ------------------------------------------------------------------ fuzzy rules
function Band({ link, review }) {
  const l = Math.max(0, Math.min(1, +link || 0)), r = review == null || review === '' ? l : Math.max(0, Math.min(l, +review))
  return (
    <span className="mr-band" aria-hidden="true">
      <span style={{ left: 0, width: `${r * 100}%`, background: 'var(--bg-3)' }} />
      <span style={{ left: `${r * 100}%`, width: `${(l - r) * 100}%`, background: '#F2C38B' }} />
      <span style={{ left: `${l * 100}%`, width: `${(1 - l) * 100}%`, background: '#8FCFA4' }} />
    </span>
  )
}

export function FuzzyRules({ rules, setRules, fields, canEdit }) {
  const setRule = (i, patch) => setRules(rules.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  const setCond = (i, k, patch) => setRule(i, { conditions: rules[i].conditions.map((c, j) => (j === k ? { ...c, ...patch } : c)) })
  const byName = Object.fromEntries(fields.map((f) => [f.name.toLowerCase(), f]))
  const names = fields.map((f) => f.name)
  return (
    <div className="stack" style={{ gap: 10 }}>
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <h2 className="sec">Fuzzy rules — a pair links when all conditions of any rule hold</h2>
        {canEdit && <button className="btn" style={{ minHeight: 30 }} onClick={() => setRules([...rules, { uid: null, name: `Rule ${rules.length + 1}`, conditions: [{ field: names[0] || '', link: 0.9, review: null }] }])}><Icon name="plus" size={13} />Rule</button>}
      </div>
      {rules.map((r, i) => (
        <div key={r.uid || i} className="mr-rule">
          <div className="row" style={{ gap: 10 }}><span className="rank">{i + 1}</span>
            <input className="in sm" style={{ width: 280, fontWeight: 600 }} value={r.name} disabled={!canEdit} onChange={(e) => setRule(i, { name: e.target.value })} aria-label={`Name of rule ${i + 1}`} />
            {canEdit && <span className="row" style={{ marginLeft: 'auto', gap: 12 }}>
              <button className="lnk" style={{ fontSize: 12 }} onClick={() => setRule(i, { conditions: [...r.conditions, { field: names[0] || '', link: 1, review: null }] })}>+ condition</button>
              <button className="btn gs-remove" onClick={() => setRules(rules.filter((_, j) => j !== i))}>Remove rule</button></span>}</div>
          <div className="mr-conds">
            <span className="mr-ch">property</span><span className="mr-ch">similarity: grey no · orange review · green link</span><span className="mr-ch">link ≥</span><span className="mr-ch">review ≥</span><span />
            {r.conditions.map((c, k) => {
              const f = byName[(c.field || '').toLowerCase()]
              return [
                <span key={`f${k}`} className="row" style={{ gap: 6, flexWrap: 'nowrap' }}>
                  {k > 0 && <span className="mr-and">AND</span>}
                  <Select className="in sm" style={{ width: 150 }} value={c.field} disabled={!canEdit} onChange={(e) => setCond(i, k, { field: e.target.value })} aria-label="Property">
                    {[...new Set([c.field, ...names])].filter(Boolean).map((n) => <option key={n}>{n}</option>)}</Select>
                  <span className="faint" style={{ fontSize: 11, color: f ? undefined : 'var(--red)' }}>{f ? f.compare : 'not compared'}</span></span>,
                <Band key={`b${k}`} link={c.link} review={c.review} />,
                <input key={`l${k}`} className="in sm mono" type="number" min="0" max="1" step="0.01" style={{ width: 72 }} value={c.link} disabled={!canEdit} onChange={(e) => setCond(i, k, { link: e.target.value === '' ? '' : +e.target.value })} aria-label="Link at" />,
                <input key={`r${k}`} className="in sm mono" type="number" min="0" max="1" step="0.01" style={{ width: 72 }} value={c.review ?? ''} placeholder="—" disabled={!canEdit} onChange={(e) => setCond(i, k, { review: e.target.value === '' ? null : +e.target.value })} aria-label="Review from" />,
                canEdit ? <button key={`x${k}`} className="ib ghost" onClick={() => setRule(i, { conditions: r.conditions.filter((_, j) => j !== k) })} aria-label="Remove condition"><Icon name="close" size={12} /></button> : <span key={`x${k}`} />,
              ]
            })}
          </div>
        </div>))}
      {!rules.length && <div className="gs-dashed">No fuzzy rules: only exact keys link.</div>}
      <div className="mr-hint">Bands: grey = no match · orange = review by a steward · green = link. The score of a pair is its weakest condition in the best rule.</div>
    </div>
  )
}

// ------------------------------------------------------------------ the Splink model and its thresholds
export function SplinkModel({ m, draft, canTrain, trainRun, onTrain, trainLabel = 'Train model…', onActivate, link, review, setLink, setReview, canEdit }) {
  return <>
    <div className="gs-box row" style={{ justifyContent: 'space-between', gap: 10 }}>
      <div><strong style={{ fontSize: 13 }}>Splink model</strong>
        <div style={{ fontSize: 12, color: 'var(--ink-2)', marginTop: 2 }}>{m ? <>version <span className="mono">{m.version}</span> · trained {short(m.trained_at)} by {m.trained_by} · {m.records} records{m.accuracy?.labels ? <> · precision {m.accuracy.precision ?? '—'} · recall {m.accuracy.recall ?? '—'} on {m.accuracy.labels} steward decisions</> : null}</>
          : 'No model yet: training estimates the prior and the weight of every comparison level from your data (the mdm_match_train job).'}</div></div>
      {onTrain && <button className="btn" disabled={!canTrain || trainRun.running} onClick={onTrain}
        title={trainLabel === 'Train model…' ? 'Trains on the saved Match Rule (the mdm_match_train job)' : 'The model is trained on the saved Match Rule, so the setup is saved first'}><Icon name="bolt" size={13} />{trainLabel}</button>}
    </div>
    <RunStatus run={trainRun.run} err={trainRun.err} />
    {draft && <div className="note blue">Trained draft <span className="mono">{draft.version}</span> ({short(draft.trained_at)}) — compare it in a full preview, then <button className="lnk" onClick={onActivate} disabled={!canTrain}>activate it</button>.</div>}
    <div className="grid2 gs-box">
      <div><label className="lbl" htmlFor="lt">Link at probability ≥ <span className="mono">{Number(link).toFixed(2)}</span></label>
        <input id="lt" type="range" min="50" max="100" style={{ width: '100%' }} value={Math.round(link * 100)} disabled={!canEdit} onChange={(e) => { const v = +e.target.value / 100; setLink(v); if (review > v) setReview(v) }} /></div>
      <div><label className="lbl" htmlFor="rt">Review from ≥ <span className="mono">{Number(review).toFixed(2)}</span></label>
        <input id="rt" type="range" min="30" max="100" style={{ width: '100%' }} value={Math.round(review * 100)} disabled={!canEdit} onChange={(e) => setReview(Math.min(+e.target.value / 100, link))} /></div>
    </div>
    {m && <details className="gs-box"><summary style={{ cursor: 'pointer', fontSize: 12.5, fontWeight: 600 }}>Learned weights</summary>
      <div className="box" style={{ marginTop: 8 }}><table className="pv"><thead><tr><th>Property</th><th>Comparison level</th><th>Match weight</th></tr></thead>
        <tbody>{m.comparisons.flatMap((c) => c.levels.map((lv, i) => <tr key={c.field + i}><td>{i === 0 ? c.field : ''}</td><td>{lv.label}</td><td><Weight w={lv.weight} /></td></tr>))}</tbody></table></div></details>}
  </>
}

// ------------------------------------------------------------------ the rule as edited, on one pair
export function TestPair({ ruleUid, pairs, body, roleLabel }) {
  const [pick, setPick] = useState(pairs.length ? 0 : -1)
  const [res, setRes] = useState(null)
  const p = pairs[pick]
  const sig = JSON.stringify([body.method, body.fields, body.fuzzy_rules, body.link_threshold, body.review_threshold, pick])
  useEffect(() => {
    if (!p) return
    const t = setTimeout(() => matchingApi.matchTestPair(ruleUid, { ...body, a: p.a, b: p.b }).then(setRes).catch((e) => setRes({ error: e.message })), 400)
    return () => clearTimeout(t)
  }, [sig])  // eslint-disable-line react-hooks/exhaustive-deps
  const ex = res?.explanation || {}
  const verdict = res?.verdict || ''
  const vcls = verdict.startsWith('linked') ? 'link' : verdict.startsWith('review') ? 'review' : 'no'
  return (
    <aside aria-label="Try it on a pair" className="gs-try">
      <h2 className="sec">Try it on a pair</h2>
      {!pairs.length && <div className="mr-hint">No pairs yet: run matching once, then the pairs it sent to review appear here.</div>}
      {pairs.length > 0 && <Select className="in" style={{ maxWidth: 520 }} value={pick} onChange={(e) => setPick(+e.target.value)} aria-label="Pair to test">
        {pairs.map((x, i) => <option key={i} value={i}>{x.status === 'Open' ? `review ${fmt(x.score)}` : x.status} · {x.a_label} ↔ {x.b_label}</option>)}</Select>}
      {res?.error && <div className="error">{res.error}</div>}
      {p && !res && <div className="faint" style={{ fontSize: 12.5 }}>Evaluating…</div>}
      {res && !res.error && <>
        <div className={`mr-verdict ${vcls}`}>{verdict}{res.decision && res.decision.status !== 'Open' ? ` · steward: ${res.decision.status}` : ''}</div>
        <div className="box" style={{ background: '#fff' }}><table className="pv">
          <thead><tr><th>Property</th><th>A</th><th>B</th></tr></thead>
          <tbody>{res.fields.filter((x) => x.role !== 'blocking' || x.a_clean || x.b_clean).map((x) => <tr key={x.field}>
            <td><div style={{ fontWeight: 600 }}>{x.field}</div>{(() => { const [l, r] = roleLabel(x.role, x.field); return <span className={`badge ${ROLE_BADGE[r]}`}>{l}</span> })()}</td>
            <td><div>{x.a ?? <span className="faint">—</span>}</div><div className="mono faint" style={{ fontSize: 11 }}>{x.a_clean ?? '(empty)'}</div></td>
            <td><div>{x.b ?? <span className="faint">—</span>}</div><div className="mono faint" style={{ fontSize: 11, color: x.equal ? 'var(--green)' : undefined }}>{x.b_clean ?? '(empty)'}{x.equal ? ' · equal' : ''}</div></td>
          </tr>)}</tbody></table></div>
        {ex.method === 'fuzzy' && <div className="stack" style={{ gap: 6 }}>
          {(ex.rules || []).map((r) => <div key={r.name} className={`mr-rres ${r.status}`}>
            <div className="row" style={{ justifyContent: 'space-between' }}><strong style={{ fontSize: 12.5 }}>{r.status === 'link' ? '✓' : r.status === 'review' ? '◐' : '✗'} {r.name}</strong>
              <span className="mono" style={{ fontSize: 11.5 }}>{r.status === 'no' ? 'no match' : `${r.status} · ${fmt(r.score)}`}</span></div>
            <div className="row" style={{ gap: 6, marginTop: 4 }}>{r.conditions.map((c, k) => <span key={k} className={`mr-cres ${c.result}`}>{c.field} {c.similarity == null ? 'empty' : fmt(c.similarity)} <span className="faint">({c.test.replace(c.field, '').trim()})</span></span>)}</div>
          </div>)}
        </div>}
        {ex.method === 'splink' && <div className="box" style={{ background: '#fff' }}><table className="pv"><thead><tr><th>Comparison</th><th>Level</th><th>Weight</th></tr></thead>
          <tbody><tr><td colSpan={2}>prior</td><td className="mono">{ex.prior}</td></tr>
            {(ex.comparisons || []).map((c) => <tr key={c.field}><td>{c.field}</td><td>{c.level || '—'}</td><td><Weight w={c.weight} /></td></tr>)}
            <tr><td colSpan={2}><strong>total → probability</strong></td><td className="mono">{ex.weight} → {fmt(ex.score, 3)}</td></tr></tbody></table></div>}
      </>}
      <div className="mr-hint">Uses the settings as edited, before saving. Steward decisions and exact keys apply on top of the score.</div>
    </aside>
  )
}
