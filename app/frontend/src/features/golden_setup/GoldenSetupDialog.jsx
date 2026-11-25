import { useEffect, useMemo, useState } from 'react'
import { Icon } from '../../components/icons.jsx'
import { Dialog, useBusy, useRun } from '../../components/Dialogs.jsx'
import { Steps } from '../../components/Steps.jsx'
import { matchingApi } from '../matching/api.js'
import { survivorshipApi } from '../survivorship/api.js'
import { goldenSetupApi } from './api.js'
import { Blocking, CMP_HELP, Cleaning, FuzzyRules, SplinkModel, TestPair, usesField } from './parts.jsx'
import SurvivorshipStep from './SurvivorshipStep.jsx'
import { Select } from '../../components/Select.jsx'
import { HELP } from '../../core/docs.js'

// Golden record setup: structure & sources → cleaning → filter & blocking → matching rules → survivorship.
// One dialog for the Match Rule and the Survivorship Rule of a Master Data node; saving writes both.

const STEP_NAMES = ['Structure & sources', 'Standardize & clean', 'Filter & blocking', 'Matching rules', 'Survivorship']
const FOOT = ['the mapping feeds every later step.', 'cleaning runs before blocking and matching.',
  'filters and blocking decide what is compared, not what matches.',
  'changing the matching rules makes the next run re-match everything (preview first).',
  'saving writes the Match Rule and the Survivorship Rule.']
const METHODS = [['exact', 'Exact keys only', 'Records link only on equal exact keys. Deterministic, no model, no review.'],
  ['fuzzy', 'Exact keys + fuzzy rules', 'Plus pairs that pass a fuzzy rule with the similarity thresholds you set.'],
  ['splink', 'Exact keys + Splink', 'Plus pairs a probabilistic model, trained on your data, scores high enough.']]
const ROLE_LABEL = { exact: 'Exact key', fuzzy: 'Similarity', splink: 'Splink', blocking: 'Not compared' }
const uniq = (xs) => [...new Set(xs.filter(Boolean))]
// A field's "Compare as": Exact key, not compared (blocking only), or its comparison. A blocking-only field
// that a fuzzy rule uses is compared there, so it shows its comparison.
const cmpOf = (f, fuzzy) => (!f ? '' : f.role === 'exact' ? 'exact'
  : f.role === 'blocking' && !fuzzy.some((r) => r.conditions.some((c) => (c.field || '').toLowerCase() === f.name.toLowerCase())) ? '' : f.comparison)

// The Match Field role behind "Compare as" and the strategy; an unchanged field keeps its role.
function roleOf(r, method) {
  if (r.compare === 'exact') return 'exact'
  if (!r.compare) return 'blocking'
  const want = method === 'splink' ? 'splink' : 'fuzzy'
  if (r.compare === r.compare0 && (r.role0 === 'blocking' || r.role0 === want)) return r.role0
  return want
}

function build(d) {
  const m = d.match, s = d.survivorship
  const fields = m?.fields || []
  const fuzzy = m?.fuzzy_rules || []
  const fieldBy = (n) => fields.find((f) => f.name.toLowerCase() === n.toLowerCase())
  const sources = uniq([...(m?.sources || []).map((x) => x.uid), ...(s?.sources || []).map((x) => x.uid)])
  const rows = []
  const cfgs = []
  for (const c of s?.rules || []) {
    const f = fieldBy(c.property)
    const mapping = Object.fromEntries(sources.map((u) => [u, c.mapping[u] || f?.mapping[u] || '']))
    rows.push({ key: c.property, name: c.property, ptype: c.ptype, golden: true, mapping, cleaning: f?.cleaning || [],
      compare: cmpOf(f, fuzzy), compare0: cmpOf(f, fuzzy), role0: f?.role, comparison: f?.comparison, fieldUid: f?.uid || null, cleaned: !!c.cleaned })
    cfgs.push(c)
  }
  for (const f of fields) {
    if (rows.some((r) => r.name.toLowerCase() === f.name.toLowerCase())) continue
    rows.push({ key: `f:${f.uid || f.name}`, name: f.name, ptype: '', golden: false, mapping: { ...f.mapping }, cleaning: f.cleaning,
      compare: cmpOf(f, fuzzy), compare0: cmpOf(f, fuzzy), role0: f.role, comparison: f.comparison, fieldUid: f.uid, cleaned: false })
  }
  return { sources, rows, cfgs, gtype: s?.golden_type?.uid || '', method: m?.method || 'fuzzy', fuzzy: m?.fuzzy_rules || [],
    processing: d.processing?.mode || 'one_machine',
    blocking: m?.blocking || [], link: m?.link_threshold ?? 0.95, review: m?.review_threshold ?? 0.7, filters: { ...(m?.filters || {}) } }
}

const START = { edit_match_rule: 2, train_matching: 4 }
// Processing of the golden records run (the Master Data node's Processing). Splink always runs on One Machine.
const PROCESSING = [['one_machine', 'One Machine', 'All records on one machine. Faster on small and medium tables; runs out of memory on very large ones.'],
  ['distributed', 'Distributed', 'The work is spread over a Spark cluster. Handles any size; takes a little longer to start on small tables.']]
const procOf = (st) => (st.method === 'splink' ? 'one_machine' : st.processing)
const SPLINK_ONE = 'Exact keys + Splink runs on One Machine processing only: training the model needs its sample on one machine.'

export default function GoldenSetupDialog({ node, ui, ctx }) {
  const [d, setD] = useState(null)
  const [st, setSt] = useState(null)
  const [step, setStep] = useState(null)
  const [counts, setCounts] = useState(null)
  const [pickMaster, setPickMaster] = useState('')
  const [busy, err, run, setErr] = useBusy()
  const load = () => goldenSetupApi.setup(node.uid).then((x) => {
    setD(x); setSt(build(x))
    setStep((s) => s || START[ui] || (x.opened_from === 'survivorship' ? 5 : 1))
  }).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [node.uid])  // eslint-disable-line react-hooks/exhaustive-deps
  const trainRun = useRun({ uid: d?.match?.uid }, { uid: d?.train_action })
  useEffect(() => { if (trainRun.done) load() }, [trainRun.done])  // eslint-disable-line react-hooks/exhaustive-deps
  const set = (patch) => setSt((x) => ({ ...x, ...patch }))

  const assigned = !!d?.master
  const match = assigned ? d.match : null, surv = assigned ? d.survivorship : null
  const rows = assigned && st ? st.rows : []
  const sources = assigned && st ? st.sources : []
  const srcBy = useMemo(() => Object.fromEntries((d?.tables || []).map((t) => [t.uid, t])), [d])
  const goldenRows = rows.filter((r) => r.golden)
  const canEdit = assigned && (!match || match.can_edit) && (!surv || surv.can_edit)

  // ---------------------------------------------------------------- what is saved
  const matchBody = () => {
    const used = new Set()
    st.fuzzy.forEach((r) => r.conditions.forEach((c) => used.add((c.field || '').toLowerCase())))
    rows.forEach((r) => { if (st.blocking.some((b) => usesField(b, r.name))) used.add(r.name.toLowerCase()) })
    const fields = rows.filter((r) => r.name.trim() && sources.some((u) => r.mapping[u])
      && (r.compare || r.cleaning.length || used.has(r.name.toLowerCase()) || !r.golden)).map((r) => ({
      uid: r.fieldUid, name: r.name.trim(), cleaning: r.cleaning,
      role: roleOf(r, st.method),
      comparison: r.compare && r.compare !== 'exact' ? r.compare : (r.comparison || 'Exact'),
      mapping: Object.fromEntries(sources.map((u) => [u, r.mapping[u] || ''])),
    }))
    return { method: st.method, sources, fields, fuzzy_rules: st.fuzzy, blocking: st.blocking, link_threshold: st.link,
      review_threshold: st.review, filters: Object.fromEntries(sources.map((u) => [u, st.filters[u] || ''])) }
  }
  const survCfgs = () => goldenRows.map((r) => {
    const c = st.cfgs.find((x) => x.property === r.name) || { property: r.name, strategy: 'sp', priority: [], recency: 'Loaded At', custom: '', ignore_empty: true, ud_wins: true, eligible: '' }
    return { ...c, ptype: r.ptype, mapping: Object.fromEntries(sources.map((u) => [u, r.mapping[u] || ''])), cleaned: r.cleaned,
      cleaning: r.cleaning, priority: (c.priority || []).filter((u) => sources.includes(u)) }
  })
  const survBody = () => ({ golden_type: st.gtype, sources, rules: survCfgs() })

  // live counts for step 3 (and the step tile)
  const countSig = st && match ? JSON.stringify([st.filters, st.blocking, sources, rows.map((r) => [r.name, r.mapping, r.cleaning])]) : ''
  useEffect(() => {
    if (!countSig) return
    setCounts(null)
    const t = setTimeout(() => matchingApi.matchBlocking(match.uid, matchBody()).then(setCounts).catch((e) => setCounts({ error: e.message })), 400)
    return () => clearTimeout(t)
  }, [countSig])  // eslint-disable-line react-hooks/exhaustive-deps

  if (!d || !st) {
    return <Dialog title="Golden record setup" help={HELP.golden_setup} onClose={ctx.close} size="full" busy={!err}><div className="dbody">{err ? <div className="error">{err}</div> : <div className="empty">Loading…</div>}</div></Dialog>
  }

  const save = (thenRun) => run(async () => {
    if (match?.can_edit) await matchingApi.saveMatchRule(match.uid, { ...matchBody(), expected: match.modified_at })
    if (surv?.can_edit) await survivorshipApi.saveRule(surv.uid, { ...survBody(), expected: surv.modified_at })
    if (d.processing?.can_edit && procOf(st) !== (d.processing.mode || 'one_machine')) await goldenSetupApi.processing(d.master.uid, procOf(st))
    const msg = `Golden record setup of ${d.master.name} saved — the next run re-matches every record (preview first)`
    if (thenRun) { ctx.notify(msg); ctx.reload(); ctx.open('run_matching', { node: { uid: d.master.uid, name: d.master.name } }) } else ctx.done(msg)
  })
  // the model is trained on the saved Match Rule: with unsaved changes (e.g. the strategy just switched to
  // Splink) both rules are saved first
  const dirty = JSON.stringify(st) !== JSON.stringify(build(d))
  const train = () => run(async () => {
    if (dirty || d.match.method !== 'splink') {
      if (match?.can_edit) await matchingApi.saveMatchRule(match.uid, { ...matchBody(), expected: match.modified_at })
      if (surv?.can_edit) await survivorshipApi.saveRule(surv.uid, { ...survBody(), expected: surv.modified_at })
      await load()
      ctx.notify('Golden record setup saved — training the model on the saved rule')
    }
    await trainRun.start({ activate: false })
  })
  const assign = () => run(async () => { const x = await goldenSetupApi.assign(node.uid, pickMaster); setD(x); setSt(build(x)); ctx.reload() })

  const setRow = (key, patch) => set({ rows: st.rows.map((r) => (r.key === key ? { ...r, ...patch } : r)) })
  const setGtype = (uid) => {
    const props = d.type_props[uid] || []
    const old = Object.fromEntries(st.rows.filter((r) => r.golden).map((r) => [r.name, r]))
    const golden = props.map((p) => old[p.name] ? { ...old[p.name], ptype: p.ptype }
      : { key: p.name, name: p.name, ptype: p.ptype, golden: true, mapping: {}, cleaning: [], compare: '', fieldUid: null, cleaned: false })
    set({ gtype: uid, rows: [...golden, ...st.rows.filter((r) => !r.golden && !props.some((p) => p.name === r.name))] })
  }
  const unmapped = goldenRows.reduce((n, r) => n + sources.filter((u) => !r.mapping[u]).length, 0)
  const exactKeys = rows.filter((r) => r.compare === 'exact').map((r) => r.name)
  const gtName = (surv?.golden_types || []).find((t) => t.uid === st.gtype)?.name || '—'
  const nFilters = sources.filter((u) => (st.filters[u] || '').trim()).length

  const red = (why) => ({ state: 'red', value: 'not set', sub: why })
  const noMatch = !match && red(assigned ? `${d.master.name} has no Match Rule` : 'assign a Master Data node first')
  const tiles = [
    assigned ? { value: gtName, sub: unmapped ? `${unmapped} column(s) not mapped` : `${goldenRows.length} properties · ${sources.length} sources · ${PROCESSING.find((p) => p[0] === procOf(st))?.[1]}`, state: unmapped ? 'warn' : '' } : red('assign a Master Data node first'),
    noMatch || { value: `${rows.filter((r) => r.cleaning.length).length} cleaning chains`, sub: 'cleaned for matching' },
    noMatch || { value: `${nFilters} filter${nFilters === 1 ? '' : 's'} · ${st.blocking.length} blocking rules`, sub: counts?.total != null ? `${counts.total.toLocaleString('en')} pairs to compare` : '' },
    noMatch || { value: METHODS.find((x) => x[0] === st.method)?.[1], sub: `exact key: ${exactKeys.join(', ') || '—'}` },
    surv ? { value: `${goldenRows.length} property rules`, sub: 'one golden record per Golden UID' } : red(assigned ? `${d.master.name} has no Survivorship Rule` : 'assign a Master Data node first'),
  ].map((t, i) => ({ ...t, title: STEP_NAMES[i], state: i + 1 === step ? (t.state === 'red' ? 'red cur' : 'cur') : t.state || (i + 1 < step ? 'done' : '') }))

  const opened = { master: 'Master Data', match: 'Match Rule', survivorship: 'Survivorship Rule' }[d.opened_from]
  const sub = assigned
    ? <>Opened on {opened} <strong>{d.node.name}</strong> <span className="mono">{d.node.uid}</span> · edits {match ? <>Match Rule “{match.name}”</> : 'no Match Rule'} and {surv ? <>Survivorship Rule “{surv.name}”</> : 'no Survivorship Rule'} of Master Data {d.master.name} together</>
    : <>Opened on {opened} <strong>{d.node.name}</strong> <span className="mono">{d.node.uid}</span> · not assigned to a Master Data node</>
  const emptyRow = (n) => <tr><td colSpan={n} className="gs-red">Not set — assign this rule to a Master Data node to list the golden record properties.</td></tr>
  const compared = rows.filter((r) => r.compare && r.compare !== 'exact').map((r) => ({ name: r.name, compare: r.compare }))

  return (
    <Dialog title="Golden record setup" size="full" labelledBy="gsT" help={HELP.golden_setup_steps[step - 1] || HELP.golden_setup} onClose={ctx.close} busy={busy} busyLabel="Working…" sub={sub}
      footer={<>
        <span style={{ fontSize: 12, color: 'var(--ink-3)' }}>Step {step} of 5 — {FOOT[step - 1]}</span>
        <div className="r">{err && <span className="error">{err}</span>}
          <button className="btn" onClick={ctx.close}>Cancel</button>
          <button className="btn" onClick={() => setStep(Math.max(1, step - 1))} disabled={step === 1}>← Back</button>
          {canEdit && <button className="btn" disabled={busy} onClick={() => save(false)}>Save</button>}
          {step < 5 ? <button className="btn pri" onClick={() => setStep(step + 1)}>Next: {STEP_NAMES[step]} →</button>
            : <button className="btn pri" disabled={!canEdit || busy} onClick={() => save(true)}>Save and run</button>}</div>
      </>}>
      <div className="gs-head"><Steps steps={tiles} onPick={setStep} /></div>
      <div className={`gs-body ${step === 5 && surv ? 'flush' : ''}`}>
        {!assigned && <div role="alert" className="gs-alert">
          <span style={{ flex: '1 1 360px' }}>This rule is not assigned to a Master Data node, so there is no golden record structure or source yet. Pick the Master Data node it belongs to; the setup is filled from it and its other rule.</span>
          <Select className="in" style={{ width: 240 }} value={pickMaster} onChange={(e) => setPickMaster(e.target.value)} aria-label="Master Data node">
            <option value="">Master Data node…</option>{d.masters.map((m) => <option key={m.uid} value={m.uid}>{m.name}{m[d.opened_from === 'match' ? 'match_rule' : 'survivorship_rule'] ? ' — replaces its current rule' : ''}</option>)}</Select>
          <button className="btn gs-remove" disabled={!pickMaster || !d.can_assign || busy} onClick={assign}>Assign and fill</button>
        </div>}

        {step === 1 && <>
          <div className="row" style={{ gap: 16, alignItems: 'flex-end' }}>
            <div style={{ width: 260 }}><label className="lbl" htmlFor="gt">Golden Record Type <span className="ptag">Link(NodeType)</span></label>
              <Select id="gt" className={`in ${assigned ? '' : 'bad'}`} value={st.gtype} disabled={!surv || !canEdit} onChange={(e) => setGtype(e.target.value)}>
                {!surv && <option value="">— not set —</option>}{(surv?.golden_types || []).map((t) => <option key={t.uid} value={t.uid}>{t.name}</option>)}</Select></div>
            <div style={{ width: 240 }}><label className="lbl" htmlFor="gproc">Processing <span className="ptag">Enum(ProcessingModes)</span></label>
              <Select id="gproc" className="in" value={procOf(st)} disabled={!assigned || !d.processing?.can_edit || st.method === 'splink'}
                onChange={(e) => set({ processing: e.target.value })} title={PROCESSING.find((p) => p[0] === procOf(st))?.[2]}>
                {PROCESSING.map(([k, l, s]) => <option key={k} value={k} title={s} disabled={k === 'distributed' && st.method === 'splink'}>{l}</option>)}</Select></div>
            <div style={{ flex: '1 1 400px' }}><span className="lbl">Sources <span className="ptag">Array(Table)</span> — one column each</span>
              <div className="row" style={{ gap: 6, minHeight: 34 }}>
                {sources.map((u, i) => <span key={u} className="chip src">{i + 1} · {srcBy[u]?.label || u}
                  {canEdit && <button className="ib ghost" style={{ width: 18, height: 18 }} onClick={() => set({ sources: sources.filter((x) => x !== u) })} aria-label={`Remove source ${srcBy[u]?.label}`}>×</button>}</span>)}
                {!assigned && <span style={{ fontSize: 12, color: 'var(--red)' }}>no sources yet</span>}
                {canEdit && <Select className="in sm dashed" value="" onChange={(e) => e.target.value && set({ sources: [...sources, e.target.value] })} aria-label="Add a source table">
                  <option value="">+ Table</option>{d.tables.filter((t) => !sources.includes(t.uid)).map((t) => <option key={t.uid} value={t.uid}>{t.label}</option>)}</Select>}
              </div></div>
          </div>
          {assigned && st.method === 'splink' && <div role="alert" className="gs-alert">{SPLINK_ONE} Processing is <strong>One Machine</strong>; pick another matching strategy (step 4) to use Distributed processing.</div>}
          <p className="gs-help"><strong>Processing</strong>: {PROCESSING.find((p) => p[0] === procOf(st))?.[2]} Both give the same golden records — if a One Machine run fails on a large table, switch to Distributed and run it again. The previews in this dialog always use One Machine processing.</p>
          <p className="gs-help">Every row is a property of the golden record. Pick the column of each source that feeds it — this one mapping is used by cleaning, matching and survivorship. A source without a column does not take part for that property.</p>
          <div className="box"><table className="pv gs-table">
            <thead><tr><th style={{ width: 220 }}>Golden record property</th>{sources.map((u, i) => <th key={u}>{i + 1} · {srcBy[u]?.label}</th>)}</tr></thead>
            <tbody>{!assigned && emptyRow(1 + sources.length)}
              {rows.map((r) => <tr key={r.key}>
                <td>{r.golden ? <div style={{ fontWeight: 600 }}>{r.name}</div>
                  : <input className="in sm" style={{ fontWeight: 600 }} value={r.name} disabled={!canEdit} placeholder="name" onChange={(e) => setRow(r.key, { name: e.target.value })} aria-label="Match-only property name" />}
                  {r.golden ? <span className="ptag">{r.ptype}</span> : <span className="badge b-grey" title="Used for matching only — not a property of the golden record">match only</span>}
                  {!r.golden && canEdit && <button className="lnk" style={{ fontSize: 11.5, marginLeft: 8, color: 'var(--red)' }} onClick={() => set({ rows: st.rows.filter((x) => x.key !== r.key) })}>remove</button>}</td>
                {sources.map((u) => <MapCell key={u} row={r} src={srcBy[u]} col={r.mapping[u] || ''} canEdit={canEdit}
                  onChange={(v) => setRow(r.key, { mapping: { ...r.mapping, [u]: v } })} />)}
              </tr>)}</tbody></table></div>
          {canEdit && match && <div className="row" style={{ gap: 10, fontSize: 12.5 }}>
            <button className="btn" style={{ minHeight: 30 }} onClick={() => set({ rows: [...st.rows, { key: `new:${Date.now()}`, name: '', ptype: '', golden: false, mapping: {}, cleaning: ['Trim', 'Lower-case'], compare: '', fieldUid: null, cleaned: false }] })}><Icon name="plus" size={13} />Match-only property</button>
            <span className="faint">compared or used for blocking, but not stored on the golden record. Golden record properties come from the Golden Record Type.</span></div>}
        </>}

        {step === 2 && <>
          <p className="gs-help">Per property, the steps that turn source values into comparable values, in order. Cleaned values are what matching compares; <strong>Golden value</strong> says whether the golden record keeps the original or the cleaned value.</p>
          {!match && assigned && <div className="gs-red">{d.master.name} has no Match Rule: cleaning belongs to it.</div>}
          <div className="box"><table className="pv gs-table">
            <thead><tr><th style={{ width: 170 }}>Golden record property</th><th>Cleaning and standardization — runs left to right</th><th style={{ width: 280 }}>Example · raw → cleaned</th><th style={{ width: 120 }}>Golden value</th></tr></thead>
            <tbody>{!assigned && emptyRow(4)}
              {rows.filter((r) => r.name.trim()).map((r) => <tr key={r.key}>
                <td><div style={{ fontWeight: 600 }}>{r.name}</div>{r.golden ? <span className="ptag">{r.ptype}</span> : <span className="badge b-grey">match only</span>}</td>
                <td><Cleaning steps={r.cleaning} options={match?.options.cleaning || []} tables={match?.options.std_tables || []} lists={match?.options.word_lists || []} disabled={!canEdit || !match} label={r.name} onChange={(cleaning) => setRow(r.key, { cleaning })} /></td>
                <td>{match && <CleanExamples ruleUid={match.uid} row={r} sources={sources} />}</td>
                <td>{r.golden ? <Select className="in sm" value={r.cleaned ? 'clean' : 'raw'} disabled={!canEdit || !surv} aria-label={`Golden value of ${r.name}`} onChange={(e) => setRow(r.key, { cleaned: e.target.value === 'clean' })}>
                  <option value="raw">original</option><option value="clean">cleaned</option></Select> : <span className="faint">—</span>}</td>
              </tr>)}</tbody></table></div>
          {match && <div className="gs-box mr-hint">Steps: {match.options.cleaning.map((o) => o.name).join(' · ')}. <strong>Drop words</strong> removes the values of an Enum (LegalForms: GmbH, Kft., S.A. …); <strong>Regex Replace</strong> takes a regular expression and its replacement. <strong>Standardize</strong> looks the value up in a Standardization Table (a node with that NodeType, e.g. Countries): found in any of its Standardization Columns → the row's Standardization Key; not found → its Default Value or the source value. An empty result never matches.</div>}
        </>}

        {step === 3 && <>
          <section className="stack" style={{ gap: 8 }}>
            <h2 className="sec">Record filter — which source records take part</h2>
            <p className="gs-help">Records that fail the filter of their source are not matched and get no golden record (for example deleted or test accounts). Leave empty to use every record. Conditions: <span className="mono">COL IS NULL</span>, <span className="mono">COL = 'x'</span>, <span className="mono">COL LIKE 'x%'</span>, <span className="mono">NOT COL</span>, joined with AND / OR.</p>
            {!assigned && <div className="gs-red">No sources yet.</div>}
            {sources.map((u) => {
              const c = counts?.sources?.find((x) => x.uid === u)
              return <div key={u} className="gs-filter">
                <span className="chip src" style={{ justifySelf: 'start' }}>{srcBy[u]?.label}</span>
                <input className="in mono" value={st.filters[u] || ''} disabled={!canEdit || !match} placeholder={`all records — e.g. ${(srcBy[u]?.columns || [])[0] || 'COL'} IS NOT NULL`}
                  onChange={(e) => set({ filters: { ...st.filters, [u]: e.target.value } })} aria-label={`Filter of ${srcBy[u]?.label}`} />
                <span style={{ fontSize: 12, color: 'var(--ink-2)', textAlign: 'right' }}>{c ? `${c.passed.toLocaleString('en')} of ${c.records.toLocaleString('en')} records` : '…'}</span>
              </div>
            })}
          </section>
          <section className="stack" style={{ gap: 8 }}>
            <h2 className="sec">Blocking rules — which pairs are compared</h2>
            <p className="gs-help">Two records are compared only when they agree on <strong>at least one</strong> rule; every other pair is skipped without being created. Exact keys always link, whatever the blocking.</p>
            {assigned && match && <Blocking blocking={st.blocking} setBlocking={(blocking) => set({ blocking })} canEdit={canEdit} counts={counts}
              fieldNames={rows.filter((r) => r.name.trim() && sources.some((u) => r.mapping[u])).map((r) => r.name)} />}
            {!assigned && <div className="gs-red">No blocking rules.</div>}
          </section>
        </>}

        {step === 4 && <>
          {st.method === 'splink' && st.processing === 'distributed' && <div role="alert" className="gs-alert">{SPLINK_ONE} This Master Data node is set to Distributed processing; with Splink its runs use <strong>One Machine</strong>.</div>}
          <div role="radiogroup" aria-label="Matching strategy" className="grid3" style={{ gap: 8 }}>
            {METHODS.map(([k, l, s]) => <button key={k} role="radio" aria-checked={st.method === k} disabled={!canEdit || !match} className={`card-radio ${st.method === k ? 'on' : ''}`} onClick={() => set({ method: k })}>
              <strong>{l}</strong><span className="sub">{s}</span></button>)}
          </div>
          <div className="box"><table className="pv gs-table">
            <thead><tr><th style={{ width: 170 }}>Property</th><th style={{ width: 210 }}>Compare as</th><th>Used as</th></tr></thead>
            <tbody>{!assigned && emptyRow(3)}
              {rows.filter((r) => r.name.trim()).map((r) => {
                const [badge, cls, note] = r.compare === 'exact' ? ['Exact key', 'b-green', 'equal after cleaning → always the same entity']
                  : !r.compare ? ['Not compared', 'b-grey', 'can still be used in blocking rules']
                    : st.method === 'exact' ? ['Ignored', 'b-grey', 'only exact keys count with this strategy']
                      : st.method === 'fuzzy' ? ['Similarity', 'b-orange', `${CMP_HELP[r.compare]} · thresholds in the rules below`]
                        : ['Splink', 'b-blue', `levels: ${match?.options.comparisons.find((c) => c.name === r.compare)?.levels || ''} — weights learned`]
                return <tr key={r.key}>
                  <td style={{ fontWeight: 600 }}>{r.name}</td>
                  <td><Select className="in sm" value={r.compare} disabled={!canEdit || !match} aria-label={`Compare ${r.name} as`} onChange={(e) => setRow(r.key, { compare: e.target.value })}>
                    <option value="">— not compared</option><option value="exact">Exact key</option>
                    {(match?.options.comparisons || []).map((c) => <option key={c.name} value={c.name}>{c.name === 'Exact' ? 'Exact (equal or not)' : c.name}</option>)}</Select></td>
                  <td style={{ fontSize: 12, color: 'var(--ink-2)' }}><span className={`badge ${cls}`}>{badge}</span> {note}</td>
                </tr>
              })}</tbody></table></div>
          {match && st.method === 'fuzzy' && <FuzzyRules rules={st.fuzzy} setRules={(fuzzy) => set({ fuzzy })} fields={compared} canEdit={canEdit} />}
          {match && st.method === 'splink' && <SplinkModel m={match.model} draft={match.draft} canTrain={match.can_train} trainRun={trainRun} canEdit={canEdit}
            onTrain={canEdit || (!dirty && d.match.method === 'splink') ? train : null}
            trainLabel={dirty || d.match.method !== 'splink' ? 'Save and train…' : 'Train model…'} onActivate={() => run(async () => { await matchingApi.matchActivate(match.uid); await load() })}
            link={st.link} review={st.review} setLink={(link) => set({ link })} setReview={(review) => set({ review })} />}
          {match && st.method === 'exact' && <div className="gs-box" style={{ fontSize: 12.5, color: 'var(--ink-2)' }}>Records link when an <strong>Exact key</strong> is equal after cleaning; everything else stays separate. No review queue, no model. Properties set to another comparison are ignored with this strategy.</div>}
          {match && <TestPair ruleUid={match.uid} pairs={match.sample_pairs || []} body={matchBody()} roleLabel={(role, field) => {
            const r = rows.find((x) => x.name.toLowerCase() === (field || '').toLowerCase())
            const k = !r ? role : r.compare === 'exact' ? 'exact' : !r.compare ? 'blocking' : st.method === 'splink' ? 'splink' : 'fuzzy'
            return [ROLE_LABEL[k], k]
          }} />}
        </>}

        {step === 5 && (surv ? <SurvivorshipStep ruleUid={surv.uid} data={surv} cfgs={survCfgs()} canEdit={canEdit} sources={sources} srcBy={srcBy} body={survBody()}
          setCfgs={(cfgs) => set({ cfgs })} />
          : <div className="gs-red">{assigned ? `${d.master.name} has no Survivorship Rule — set one on the Master Data node.` : 'No golden record properties yet.'}</div>)}
      </div>
    </Dialog>
  )
}

function MapCell({ row, src, col, canEdit, onChange }) {
  return (
    <td>
      <Select className={`in sm mono ${col ? '' : 'unmapped'}`} value={col} disabled={!canEdit} onChange={(e) => onChange(e.target.value)} aria-label={`${row.name || 'property'} from ${src?.label}`}>
        <option value="">— not mapped</option>{(src?.columns || []).map((c) => <option key={c}>{c}</option>)}</Select>
      {!col && <div className="gs-cellnote">does not feed {row.name || 'it'}</div>}
    </td>
  )
}

function CleanExamples({ ruleUid, row, sources }) {
  const [res, setRes] = useState(null)
  const mapping = Object.fromEntries(sources.map((u) => [u, row.mapping[u] || '']))
  const sig = JSON.stringify([row.cleaning, mapping])
  useEffect(() => {
    if (!sources.some((u) => mapping[u])) { setRes({ samples: [] }); return }
    const t = setTimeout(() => matchingApi.matchClean(ruleUid, { field: { cleaning: row.cleaning, mapping } }).then(setRes).catch((e) => setRes({ error: e.message })), 350)
    return () => clearTimeout(t)
  }, [sig])  // eslint-disable-line react-hooks/exhaustive-deps
  if (!res) return <span className="faint" style={{ fontSize: 12 }}>…</span>
  if (res.error) return <span className="error" style={{ fontSize: 12 }}>{res.error}</span>
  const ex = res.samples.flatMap((s) => s.values.slice(0, 1)).slice(0, 2)
  if (!ex.length) return <span className="faint" style={{ fontSize: 12 }}>no values</span>
  return ex.map((v, i) => <div key={i} className="gs-ex">{v.raw} <span className="faint">→</span> <span className="mono" style={{ color: v.clean == null ? 'var(--red)' : 'var(--green)' }}>{v.clean ?? '(empty)'}</span></div>)
}
