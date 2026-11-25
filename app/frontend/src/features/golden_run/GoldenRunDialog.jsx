import { useEffect, useRef, useState } from 'react'
import { Icon } from '../../components/icons.jsx'
import { Dialog, useBusy } from '../../components/Dialogs.jsx'
import { Steps } from '../../components/Steps.jsx'
import { api } from '../../core/api.js'
import { goldenRecordsApi } from '../golden_records/api.js'
import { goldenSetupApi } from '../golden_setup/api.js'
import { matchingApi } from '../matching/api.js'
import { fmt, short } from '../matching/shared.jsx'
import { goldenRunApi } from './api.js'
import RunResult from './RunResult.jsx'
import { Select } from '../../components/Select.jsx'
import { HELP } from '../../core/docs.js'

// Golden records run: Load → Match → Review → Survivorship → Export of a Master Data node.
// "Run <step>" runs just the selected step (Review is the stewards' step: nothing to run); "Run all" runs every step.

const NAMES = ['Load', 'Match', 'Review', 'Survivorship', 'Export']
const ONLY = { 1: 'load', 2: 'match', 4: 'survivorship', 5: 'export' }
const LAST = { load: 1, match: 2, survivorship: 4, export: 5 }
const ACT = { Create: 'b-green', Update: 'b-orange', Unchanged: 'b-grey', Retire: 'b-red', 'Keep (orphan)': 'b-grey' }
const DONE = ['SUCCESS', 'FAILED', 'CANCELED']
const START = { run_matching: 2 }

export default function GoldenRunDialog({ node, ui, ctx }) {
  const [v, setV] = useState(null)
  const [step, setStep] = useState(null)
  const [mode, setMode] = useState('incremental')
  const [preview, setPreview] = useState(true)
  const [scope, setScope] = useState('changed')
  const [retire, setRetire] = useState(true)
  const [skip, setSkip] = useState({})
  const [plan, setPlan] = useState(null)
  const [exp, setExp] = useState({})
  const [lastLog, setLastLog] = useState(null)
  const [flow, setFlow] = useState(null)
  const [ran, setRan] = useState({})
  const [pickMaster, setPickMaster] = useState('')
  const [busy, err, run, setErr] = useBusy()
  const timer = useRef()
  const load = () => goldenRunApi.view(node.uid).then((x) => {
    setV(x)
    setStep((s) => s || START[ui] || (x.opened_from === 'survivorship' ? 4 : 1))
  }).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [node.uid])  // eslint-disable-line react-hooks/exhaustive-deps
  const master = v?.master
  const finished = flow && DONE.includes(flow.state)
  useEffect(() => {
    if (!master) return
    matchingApi.matchLog(master.uid, 6).then(setLastLog).catch(() => setLastLog([]))
  }, [master?.uid, finished])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!master || !v.survivorship) return
    const t = setTimeout(() => goldenRecordsApi.goldenPlan(master.uid, undefined, retire).then((p) => {
      setPlan(p); setExp((e) => (Object.keys(e).length ? e : { [p.groups[0]?.name]: true }))
    }).catch((e) => setPlan({ error: e.message })), 200)
    return () => clearTimeout(t)
  }, [master?.uid, retire, finished])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!flow || finished) return
    timer.current = setTimeout(() => api.run(flow.run_id).then((f) => {
      setFlow({ ...f, preview: flow.preview })
      if (DONE.includes(f.state)) {
        load()
        if (!flow.preview) setRan((r) => ({ ...r, ...Object.fromEntries(f.steps.filter((s) => s.state === 'SUCCESS').map((s) => [LAST[s.key], true])) }))
      }
    }).catch((e) => setErr(e.message)), 1500)
    return () => clearTimeout(timer.current)
  }, [flow])  // eslint-disable-line react-hooks/exhaustive-deps

  if (!v || !step) return <Dialog title="Golden records run" help={HELP.golden_run} onClose={ctx.close} size="full" busy={!err}><div className="dbody">{err ? <div className="error">{err}</div> : <div className="empty">Loading…</div>}</div></Dialog>

  const assigned = !!master
  const running = flow && !finished
  const start = (which) => run(async () => {
    const tables = (v.tables || []).filter((t) => t.can_load && !skip[t.uid]).map((t) => t.uid)
    const f = await goldenRunApi.run(master.uid, { ...which, preview, mode, retire, scope, tables })
    setFlow({ ...f, preview })
  })
  const close = () => { ctx.close(); ctx.reload() }
  const assign = () => run(async () => { await goldenSetupApi.assign(node.uid, pickMaster); await load(); ctx.reload() })

  const m = v.match, s = v.survivorship
  const counts = plan && !plan.error ? Object.entries(plan.counts).map(([k, n]) => `${n} ${k.toLowerCase()}`).join(' · ') : ''
  const records = (v.tables || []).reduce((n, t) => n + t.records, 0)
  const VAL = assigned ? [
    [`${v.tables.length} source tables`, `${records.toLocaleString('en')} records`],
    [m ? (mode === 'full' ? 'full re-match' : 'incremental') : 'no Match Rule', m?.needs_full ? 'rule changed — run a full re-match' : m?.method || ''],
    [m ? `${m.open_count} pairs open` : '—', 'stewards decide'],
    [s ? (counts || '…') : 'no Survivorship Rule', s?.name || ''],
    [`${v.exports.length} export node${v.exports.length === 1 ? '' : 's'}`, 'refreshed after a change'],
  ] : []
  const tiles = NAMES.map((t, i) => {
    const n = i + 1
    if (!assigned) return { title: t, value: 'not set', sub: 'no Master Data node', state: n === step ? 'red cur' : 'red' }
    const done = !!ran[n]
    return { title: t, value: done ? 'done · just now' : VAL[i][0], sub: VAL[i][1],
      state: n === step ? 'cur' : done ? 'done' : n === 3 && m?.open_count ? 'warn' : '' }
  })
  const verb = preview ? 'Preview' : 'Run'
  const stepName = NAMES[step - 1]
  const runTip = step === 3 ? 'Review is done by stewards — there is nothing to run'
    : preview ? (step === 2 ? 'Previews matching: nothing is written' : 'Only Match has a preview')
    : `Runs ${stepName} only`
  const allTip = preview ? 'Previews matching: nothing is written' : 'Runs Load → Match → Survivorship → Export (Review is skipped)'
  const cantRunStep = step === 3 || (preview && step !== 2)
  const opened = { master: 'Master Data', match: 'Match Rule', survivorship: 'Survivorship Rule' }[v.opened_from]

  return (
    <Dialog title="Golden records run" help={HELP.golden_run} size="full" labelledBy="grT" onClose={close} busy={busy} busyLabel="Starting…"
      sub={assigned ? <>Opened on {opened} <strong>{v.node.name}</strong> <span className="mono">{v.node.uid}</span> · Master Data {master.name} · <span title={master.processing_note || 'Set in Golden record setup, step 1'}>{master.processing || 'One Machine'} processing</span> · Load → Match → Review → Survivorship → Export in one place</>
        : <>Opened on {opened} <strong>{v.node.name}</strong> <span className="mono">{v.node.uid}</span> · not assigned to a Master Data node</>}
      footer={<>
        <label className="row" style={{ gap: 8, fontSize: 13 }}><input type="checkbox" checked={preview} onChange={() => setPreview(!preview)} />Preview only — change nothing</label>
        <div className="r">{err && <span className="error">{err}</span>}
          <span style={{ fontSize: 12, color: 'var(--ink-3)' }}>one background run · Review is skipped (it waits for stewards)</span>
          <button className="btn" onClick={close}>Close</button>
          <button className="btn" title={runTip} disabled={!assigned || running || busy || cantRunStep} onClick={() => start({ only: ONLY[step] })}>{verb} {stepName}</button>
          <button className="btn pri" title={allTip} disabled={!assigned || running || busy} onClick={() => start({ until: 'export' })}><Icon name="bolt" size={13} />{verb} all</button></div>
      </>}>
      <div className="gs-head"><Steps steps={tiles} onPick={setStep} /></div>
      <div className="gs-body">
        {!assigned && <div role="alert" className="gs-alert">
          <span style={{ flex: '1 1 360px' }}>This rule is not assigned to a Master Data node, so there is nothing to load, match or build. Assign it to a Master Data node (or open Golden record setup) first.</span>
          <Select className="in" style={{ width: 240 }} value={pickMaster} onChange={(e) => setPickMaster(e.target.value)} aria-label="Master Data node">
            <option value="">Master Data node…</option>{v.masters.map((x) => <option key={x.uid} value={x.uid}>{x.name}{x[v.opened_from === 'match' ? 'match_rule' : 'survivorship_rule'] ? ' — replaces its current rule' : ''}</option>)}</Select>
          <button className="btn gs-remove" disabled={!pickMaster || !v.can_assign || busy} onClick={assign}>Assign</button>
          <button className="btn" onClick={() => ctx.open(v.opened_from === 'match' ? 'edit_match_rule' : 'edit_rule', { node })}>Golden record setup</button>
        </div>}
        {flow && <FlowStatus flow={flow} />}

        {step === 1 && <>
          <p className="gs-help">The source tables of this Master Data node. Loading runs the <span className="mono">mdm_load</span> pipeline for each ticked table and merges changed records by record hash.</p>
          {!assigned ? <div className="gs-red">No source tables.</div> : <div className="box"><table className="pv gs-table">
            <thead><tr><th>Source table</th><th>Records</th><th>Last load</th><th>Load mode</th><th style={{ width: 80 }}>Load</th></tr></thead>
            <tbody>{v.tables.map((t) => {
              const st = t.last_load?.stats || {}
              return <tr key={t.uid}>
                <td><div style={{ fontWeight: 600 }}>{t.label}</div><div className="mono faint" style={{ fontSize: 11 }}>{t.table_name || 'no Table Name — Configure Load'}</div></td>
                <td className="mono">{t.records.toLocaleString('en')}</td>
                <td>{t.last_load ? <><div className="mono">{short(t.last_load.started_at)}</div><div className="faint" style={{ fontSize: 11 }}>{t.last_load.status} · {st.inserted ?? 0} new · {st.updated ?? 0} changed · {st.deleted ?? 0} deleted</div></> : <span className="faint">not logged yet</span>}</td>
                <td>{t.load_mode}{t.schedule ? <span className="faint"> · schedule <span className="mono">{t.schedule}</span></span> : ''}</td>
                <td><label className="row" style={{ gap: 6, fontSize: 12.5 }}><input type="checkbox" checked={t.can_load && !skip[t.uid]} disabled={!t.can_load} onChange={() => setSkip({ ...skip, [t.uid]: !skip[t.uid] })} aria-label={`Load ${t.label}`} />load</label></td>
              </tr>
            })}</tbody></table></div>}
        </>}

        {step === 2 && <>
          <div role="radiogroup" aria-label="Mode" className="grid2" style={{ gap: 8 }}>
            {[['incremental', 'Incremental', 'records whose match properties changed since the last run, new and deleted records, pending steward decisions — and the entities they touch'],
              ['full', 'Full re-match', `all source records${m?.needs_full ? ' — the Match Rule changed after the last run, so the next run re-matches everything anyway' : ' — needed after the Match Rule changes'}`]].map(([k, l, sub]) => (
              <button key={k} role="radio" aria-checked={mode === k} className={`card-radio ${mode === k ? 'on' : ''}`} onClick={() => setMode(k)}><strong>{l}</strong><span className="sub">{sub}</span></button>))}
          </div>
          {!assigned && <div className="gs-red">No Match Rule result yet.</div>}
          {assigned && !m && <div className="gs-red">{master.name} has no Match Rule.</div>}
          {assigned && m && <>
            <div className="row" style={{ justifyContent: 'space-between' }}><span className="faint" style={{ fontSize: 12 }}>Match Rule {m.name} · {m.method} · {master.processing || 'One Machine'} processing{m.last_run ? ` · last run ${m.last_run}` : ' · not run yet'}</span></div>
            {flow?.log?.some((r) => r.activity === 'match') ? <RunResult log={flow.log} />
              : lastLog?.some((r) => r.activity === 'match') ? <RunResult log={lastLog} title={`Last run · ${short(lastLog.find((r) => r.activity === 'match').started_at)} · ${lastLog.find((r) => r.activity === 'match').triggered_by}`} />
                : <div className="faint" style={{ fontSize: 12.5 }}>No run logged yet — preview a run to see what it would change.</div>}
          </>}
        </>}

        {step === 3 && (!assigned || !m ? <div className="gs-red">No pairs to review.</div> : <>
          <div className="gs-banner">
            <span><strong>{m.open_count} pair{m.open_count === 1 ? '' : 's'}</strong> scored between review {fmt(m.review)} and link {fmt(m.link)} wait for a steward. Until decided they stay in separate entities; decisions take effect in the next Match.</span>
            <button className="btn" disabled={!v.can.review} onClick={() => ctx.open('review_matches', { node: { uid: master.uid, name: master.name } })}>Open review queue</button>
          </div>
          <div className="box"><table className="pv gs-table">
            <thead><tr><th>Record A</th><th>Record B</th><th>Score</th><th>Rule</th><th>Waiting since</th></tr></thead>
            <tbody>{m.open.map((p) => <tr key={p.uid}><td>{p.a.label}<div className="faint" style={{ fontSize: 11 }}>{p.a.name}</div></td><td>{p.b.label}<div className="faint" style={{ fontSize: 11 }}>{p.b.name}</div></td>
              <td className="mono">{fmt(p.score)}</td><td style={{ color: 'var(--ink-2)' }}>{p.reason || '—'}</td><td className="mono">{short(p.modified_at)}</td></tr>)}
              {!m.open.length && <tr><td colSpan={5} className="faint">Nothing waits for a steward.</td></tr>}</tbody></table></div>
          <div className="mr-hint">Review is the stewards' step: the run does not wait for it. Survivorship builds golden records from the entities as they are now. {m.decided_count} decided pair(s) so far.</div>
        </>)}

        {step === 4 && <>
          <div className="row" style={{ gap: '14px 22px', alignItems: 'flex-end' }}>
            <div style={{ width: 300 }}><label className="lbl" htmlFor="scope">Scope</label>
              <Select id="scope" className="in" value={scope} onChange={(e) => setScope(e.target.value)}>
                <option value="changed">Entities changed since the last refresh</option><option value="all">Full rebuild (all source records)</option></Select></div>
            <label className="row" style={{ gap: 8, fontSize: 13, height: 34 }}><input type="checkbox" checked={retire} onChange={() => setRetire(!retire)} />Retire golden records whose sources are gone</label>
            {s && <span style={{ fontSize: 12.5, color: 'var(--ink-2)', height: 34, display: 'inline-flex', alignItems: 'center' }}>Survivorship Rule <strong style={{ margin: '0 4px' }}>{s.name}</strong> · {s.rules} property rules</span>}
          </div>
          {!assigned && <div className="gs-red">No golden records to build.</div>}
          {assigned && !s && <div className="gs-red">{master.name} has no Survivorship Rule.</div>}
          {plan?.error && <div className="error">{plan.error}</div>}
          {assigned && s && plan && !plan.error && <GoldenPlan plan={plan} master={master} scope={scope} exp={exp} setExp={setExp} counts={counts} />}
        </>}

        {step === 5 && <>
          <p className="gs-help">Export nodes that read this Master Data node; they are refreshed after the golden records change (views are created by the app, the rest by the <span className="mono">mdm_export</span> job).</p>
          {!assigned ? <div className="gs-red">No exports.</div> : <div className="box"><table className="pv gs-table">
            <thead><tr><th>Export node</th><th>Type</th><th>Publishes</th><th>Last run</th></tr></thead>
            <tbody>{v.exports.map((e) => <tr key={e.uid}><td style={{ fontWeight: 600 }}>{e.name}</td><td>{e.type}</td><td className="mono" style={{ fontSize: 11.5 }}>{e.location || '—'}</td><td style={{ color: 'var(--ink-2)' }}>{e.last_run || '—'}</td></tr>)}
              {!v.exports.length && <tr><td colSpan={4} className="faint">No Export node has this Master Data node as its Root Node.</td></tr>}</tbody></table></div>}
        </>}
      </div>
    </Dialog>
  )
}

function FlowStatus({ flow }) {
  const ok = flow.state === 'SUCCESS', fail = flow.state === 'FAILED'
  return (
    <div className={`runbar ${ok ? 'ok' : fail ? 'fail' : ''}`} style={{ flexWrap: 'wrap' }}>
      {!ok && !fail && <span className="spin" />}
      <strong>{flow.preview ? 'Preview' : 'Run'} · {flow.state}</strong>
      <span className="row" style={{ gap: 6, flex: 1 }}>{flow.steps.map((x, i) => (
        <span key={i} className={`badge ${x.state === 'SUCCESS' ? 'b-green' : x.state === 'FAILED' ? 'b-red' : x.state === 'RUNNING' ? 'b-blue' : 'b-grey'}`} title={x.message || ''}>
          {x.label} · {x.state.toLowerCase()}</span>))}</span>
      {fail && <span className="mono" style={{ fontSize: 11.5, width: '100%' }}>{flow.message}</span>}
      {flow.steps.filter((x) => x.url).slice(-1).map((x) => <a key={x.url} href={x.url} target="_blank" rel="noreferrer">Open run</a>)}
    </div>
  )
}

function GoldenPlan({ plan, master, scope, exp, setExp, counts }) {
  const groups = plan.groups || []
  return (
    <div className="stack">
      <div className="row" style={{ justifyContent: 'space-between' }}><h2 className="sec">Dry run — children of {master.name} after the refresh</h2>
        <span style={{ fontSize: 12, color: 'var(--ink-2)' }}>{counts}{plan.unmatched ? ` · ${plan.unmatched} record(s) not matched yet — run Match first` : ''}</span></div>
      <div className="box"><table className="pv gs-table">
        <thead><tr><th style={{ width: 24 }} /><th>Golden record</th><th>Change</th><th>Source records (Golden UID)</th><th>Match score</th><th>Changed properties</th></tr></thead>
        <tbody>{groups.map((g) => {
          const on = !!exp[g.name]
          const skipped = scope === 'changed' && g.action === 'Unchanged'
          return [
            <tr key={g.name + g.uid} style={{ background: g.action === 'Retire' ? '#FFF8F7' : g.action === 'Create' ? '#F6FBF7' : undefined }}>
              <td><button className="ib ghost" onClick={() => setExp({ ...exp, [g.name]: !on })} aria-expanded={on} aria-label={`Show values for ${g.name}`}>{on ? '▾' : '▸'}</button></td>
              <td><div style={{ fontWeight: 600 }}>{g.name}</div><div className="mono faint" style={{ fontSize: 11 }}>{g.uid || 'new · MDM-…'}</div></td>
              <td><span className={`badge ${ACT[g.action]}`}>{g.action}{skipped ? ' · not read (no source change)' : ''}</span></td>
              <td><div className="row" style={{ gap: 4 }}>{g.records.map((r) => <span key={r.uid} className="chip src" title={r.title}>{r.label}</span>)}</div>
                {g.notes.map((n) => <div key={n} style={{ fontSize: 11, color: 'var(--orange)', marginTop: 4 }}>{n}</div>)}</td>
              <td className="mono">{g.score}</td><td style={{ color: 'var(--ink-2)' }}>{g.changes}</td>
            </tr>,
            on && g.values.length > 0 && <tr key={g.name + ':v'}><td colSpan={6} style={{ background: '#FAFBFC', padding: '6px 10px 10px 44px' }}>
              <table className="gs-vals"><tbody>{g.values.map((x) => <tr key={x.prop}><td style={{ width: 150, color: 'var(--ink-2)' }}>{x.prop}</td>
                <td style={{ fontWeight: x.value ? 500 : 400, color: x.value ? undefined : 'var(--ink-4)', fontStyle: x.value ? undefined : 'italic' }}>{x.value || '(empty)'}</td>
                <td style={{ color: 'var(--ink-3)' }}>{x.source} · {x.rule}</td></tr>)}</tbody></table></td></tr>,
          ]
        })}
          {!groups.length && <tr><td colSpan={6} className="faint">No source records yet — load the source tables first.</td></tr>}</tbody>
      </table></div>
      {plan.conflicts?.length > 0 && <div className="error">Values that could not be stored: {plan.conflicts.join('; ')}</div>}
    </div>
  )
}
