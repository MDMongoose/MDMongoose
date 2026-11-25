import { useEffect, useState } from 'react'
import { ICON_NAMES, Icon } from '../../components/icons.jsx'
import { Dialog, Field, RunStatus, useBusy, useRun } from '../../components/Dialogs.jsx'
import { configureLoadApi } from './api.js'
import { Select } from '../../components/Select.jsx'
import { HELP } from '../../core/docs.js'

// Configure Load: the source table of a Table node, its key, name, column mapping, load mode, schedule
// and delete behaviour, with a preview.

export default function LoadDialog({ node, actions, onClose, onSaved }) {
  const [c, setC] = useState(null)
  const [cat, setCat] = useState({ catalogs: [], schemas: [], tables: [] })
  const [cols, setCols] = useState([])
  const [pv, setPv] = useState(null)
  const [cdf, setCdf] = useState(undefined)
  const [busy, err, run, setErr] = useBusy()
  const loadAction = actions.find((a) => a.ui === 'load_children')
  const loader = useRun(node, loadAction || { uid: '' })

  useEffect(() => { configureLoadApi.loadConfig(node.uid).then(setC).catch((e) => setErr(e.message)) }, [node.uid])
  useEffect(() => {
    if (!c) return
    configureLoadApi.sourceCatalog(c.catalog, c.schema).then(setCat).catch(() => {})
  }, [c?.catalog, c?.schema])
  useEffect(() => {
    if (!c?.table) { setCols([]); return }
    configureLoadApi.loadPreview(node.uid, { ...c, mapping: [] }).then((p) => setCols(p.columns)).catch(() => setCols([]))
  }, [c?.catalog, c?.schema, c?.table])
  useEffect(() => {
    if (!c?.table || !c?.catalog || !c?.schema) { setCdf(undefined); return }
    setCdf(undefined)
    configureLoadApi.sourceCdf(`${c.catalog}.${c.schema}.${c.table}`).then((r) => setCdf(r.cdf)).catch(() => setCdf(null))
  }, [c?.catalog, c?.schema, c?.table])
  if (!c) return <Dialog title="Configure Load" help={HELP.configure_load} onClose={onClose} busy={!err}><div className="dbody">{err ? <div className="error">{err}</div> : <div className="empty">Loading…</div>}</div></Dialog>

  const set = (p) => setC({ ...c, ...p })
  const props = c.record_type?.props || []
  const mapping = c.mapping
  const setMap = (i, p) => set({ mapping: mapping.map((m, j) => (j === i ? { ...m, ...p } : m)) })
  const editable = c.can_edit
  const save = (thenLoad) => run(async () => {
    const r = await configureLoadApi.saveLoadConfig(node.uid, { ...c, expected: c.modified_at })
    if (thenLoad && loadAction) { await loader.start(); return }
    onSaved(`Load configuration saved — pipeline dataset ${r.dataset} is updated on the next load`)
  })
  return (
    <Dialog title="Configure Load" help={HELP.configure_load} labelledBy="dpt" onClose={onClose} busy={busy} busyLabel="Working…"
      sub={<>Table <strong style={{ color: 'var(--ink)' }}>{c.name}</strong> <span className="mono">{c.uid}</span> under {c.path} (types: {c.types.join(' + ')}). Action of NodeType Table · <span className="mono">dataset {c.dataset}</span>. Children are loaded as <strong style={{ color: 'var(--ink)' }}>Record{c.record_type ? ' + ' + c.record_type.name : ''}</strong> nodes (origin: loaded).</>}
      footer={<>
        <button className="btn" disabled={!c.table} onClick={() => run(async () => setPv(await configureLoadApi.loadPreview(node.uid, c)))}>Preview 10 rows</button>
        <div className="r"><button className="btn" onClick={onClose}>{loader.done ? 'Close' : 'Cancel'}</button>
          {loadAction && <button className="btn" disabled={!editable || busy || loader.running || cdf === false} onClick={() => save(true)}><Icon name="bolt" size={13} />Save &amp; load now</button>}
          <button className="btn pri" disabled={!editable || busy || cdf === false} onClick={() => save(false)}>Save &amp; update pipeline</button></div>
      </>}>
      <div className="dbody" style={{ gap: 18 }}>
        <div className="stack" style={{ gap: 10 }}>
          <div className="row" style={{ justifyContent: 'space-between' }}>
            <h2 className="sec">1 · Source</h2>
            {c.table && cdf === true && <span className="cdf on" title="delta.enableChangeDataFeed = true">✓ Change data feed on</span>}
            {c.table && cdf === false && <span className="cdf off" title="The load reads the table's change data feed">Change data feed off</span>}
          </div>
          <div className="note blue" style={{ fontSize: 12 }}>The source must be a Delta table with change data feed enabled — usually in <span className="mono">mdm_staging</span>, loaded there by an upstream process. Only its changes are read on each load.</div>
          {c.table && cdf === false && <div className="error" style={{ fontSize: 12 }}>Enable it first: <span className="mono">ALTER TABLE {c.catalog}.{c.schema}.{c.table} SET TBLPROPERTIES (delta.enableChangeDataFeed = true)</span></div>}
          <div className="grid3">
            <Field label="Catalog" htmlFor="cat"><Select id="cat" className="in" value={c.catalog} onChange={(e) => set({ catalog: e.target.value, schema: '', table: '' })}>
              {[c.catalog, ...cat.catalogs.filter((x) => x !== c.catalog)].filter(Boolean).map((x) => <option key={x}>{x}</option>)}{!c.catalog && <option value="">—</option>}</Select></Field>
            <Field label="Schema" htmlFor="sch"><Select id="sch" className="in" value={c.schema} onChange={(e) => set({ schema: e.target.value, table: '' })}>
              <option value="">—</option>{[...new Set([c.schema, ...cat.schemas])].filter(Boolean).map((x) => <option key={x}>{x}</option>)}</Select></Field>
            <Field label="Table" htmlFor="tbl"><Select id="tbl" className="in" value={c.table} onChange={(e) => set({ table: e.target.value })}>
              <option value="">—</option>{[...new Set([c.table, ...cat.tables])].filter(Boolean).map((x) => <option key={x}>{x}</option>)}</Select></Field>
            <Field label="Key column → source key" htmlFor="key">
              <Select id="key" className="in mono" value={c.keys[0] || ''} onChange={(e) => set({ keys: [e.target.value, ...c.keys.slice(1)].filter(Boolean) })}>
                <option value="">—</option>{[...new Set([...c.keys, ...cols])].map((x) => <option key={x}>{x}</option>)}</Select></Field>
            <Field label="NAME expression" style={{ gridColumn: 'span 2' }} htmlFor="nm"><input id="nm" className="in mono" value={c.name_expression} placeholder="KUNNR || ' · ' || NAME1" onChange={(e) => set({ name_expression: e.target.value })} /></Field>
            <Field label="Child type (Record Type)" htmlFor="ct"><Select id="ct" className="in" value={c.record_type?.uid || ''} onChange={(e) => set({ record_type: { uid: e.target.value, name: c.record_types.find((t) => t.uid === e.target.value)?.name, props: [] } })}>
              <option value="">Record</option>{c.record_types.map((t) => <option key={t.uid} value={t.uid}>{t.name}</option>)}</Select></Field>
            <Field label="Expectations" hint="one SQL condition per line; failing rows are dropped" style={{ gridColumn: 'span 2' }} htmlFor="exp">
              <textarea id="exp" className="in mono" rows={2} value={c.expectations.join('\n')} onChange={(e) => set({ expectations: e.target.value.split('\n') })} /></Field>
          </div>
        </div>
        <div className="stack" style={{ gap: 10 }}>
          <h2 className="sec">2 · Column → property mapping</h2>
          <div className="box">
            <table className="pv">
              <thead><tr><th>Source column</th><th>Property (from child type)</th><th>Transform</th><th style={{ width: 32 }} /></tr></thead>
              <tbody>
                {c.keys.map((k) => <tr key={'k' + k}><td className="mono">{k}</td><td className="mono">source_key → UID match</td><td className="faint">—</td><td /></tr>)}
                {mapping.map((m, i) => (
                  <tr key={i}>
                    <td><Select className="in sm mono" value={m.source} onChange={(e) => setMap(i, { source: e.target.value, property: m.property || e.target.value })}>
                      <option value="">—</option>{[...new Set([m.source, ...cols])].filter(Boolean).map((x) => <option key={x}>{x}</option>)}</Select></td>
                    <td><input className="in sm mono" list="recprops" value={m.property} onChange={(e) => setMap(i, { property: e.target.value })} /></td>
                    <td><Select className="in sm" value={m.transform} onChange={(e) => setMap(i, { transform: e.target.value })}><option>—</option>{c.transforms.filter((t) => t !== '—').map((t) => <option key={t}>{t}</option>)}</Select></td>
                    <td><button className="ib" onClick={() => set({ mapping: mapping.filter((_, j) => j !== i) })} aria-label="Remove mapping">×</button></td>
                  </tr>
                ))}
                {mapping.length === 0 && <tr><td colSpan={4} className="faint">No mapping — every source column becomes a Record property with its own name.</td></tr>}
              </tbody>
            </table>
            <datalist id="recprops">{props.map((p) => <option key={p} value={p} />)}</datalist>
          </div>
          <div className="row">
            <button className="lnk" onClick={() => set({ mapping: [...mapping, { source: '', property: '', transform: '—' }] })}>+ Map column</button>
            {cols.length > 0 && <button className="lnk" onClick={() => set({ mapping: cols.filter((x) => !c.keys.includes(x)).map((x) => ({ source: x, property: props.includes(x) ? x : x, transform: '—' })) })}>Map all columns 1:1</button>}
          </div>
        </div>
        <div className="stack" style={{ gap: 10 }}>
          <h2 className="sec">3 · Load</h2>
          <div role="radiogroup" aria-label="Load mode" className="row" style={{ flexWrap: 'nowrap' }}>
            <button role="radio" aria-checked={c.load_mode === 'incremental'} className={`card-radio ${c.load_mode === 'incremental' ? 'on' : ''}`} style={{ flex: 1 }} onClick={() => set({ load_mode: 'incremental' })}><strong>Incremental (change data feed)</strong><span className="sub">only inserts, updates and deletes committed to the source since the last load</span></button>
            <button role="radio" aria-checked={c.load_mode === 'full'} className={`card-radio ${c.load_mode === 'full' ? 'on' : ''}`} style={{ flex: 1 }} onClick={() => set({ load_mode: 'full' })}><strong>Full reload</strong><span className="sub">rebuilds the dataset from the whole source and compares it with the loaded records</span></button>
          </div>
          <div className="grid2">
            <Field label="Schedule (cron, UTC)" hint="run by the hourly mdm_refresh job" htmlFor="cron"><input id="cron" className="in mono" value={c.schedule} placeholder="0 2 * * *" onChange={(e) => set({ schedule: e.target.value })} /></Field>
            <Field label="When a source row is deleted" htmlFor="del"><Select id="del" className="in" value={c.delete_mode} onChange={(e) => set({ delete_mode: e.target.value })}>
              <option value="soft">Soft-delete data node</option><option value="hard">Hard delete</option><option value="keep">Keep</option></Select></Field>
            <Field label="Icon for loaded Record nodes" htmlFor="dicon"><Select id="dicon" className="in" value={c.record_icon} onChange={(e) => set({ record_icon: e.target.value })}>
              <option value="">Inherit from Record type</option>{ICON_NAMES.map((i) => <option key={i}>{i}</option>)}</Select></Field>
            <Field label="Max child depth of this table" htmlFor="dmax"><input id="dmax" type="number" min="1" className="in" value={c.max_child_depth ?? 1} onChange={(e) => set({ max_child_depth: e.target.value })} /></Field>
          </div>
          <label className="row" style={{ gap: 8, fontSize: 13 }}><input type="checkbox" checked={c.run_golden} onChange={() => set({ run_golden: !c.run_golden })} />Run matching &amp; golden-record build after each load</label>
        </div>
        {pv && (
          <div className="stack">
            <h2 className="sec">Preview · first {pv.records.length} rows as Record nodes</h2>
            <div className="box" style={{ maxHeight: 260 }}>
              <table className="pv">
                <thead><tr><th>Source key</th><th>Name</th>{Object.keys(pv.records[0]?.properties || {}).map((k) => <th key={k}>{k}</th>)}</tr></thead>
                <tbody>{pv.records.map((r, i) => <tr key={i}><td className="mono">{r.key}</td><td>{r.name}</td>{Object.values(r.properties).map((v, j) => <td key={j}>{v ?? ''}</td>)}</tr>)}</tbody>
              </table>
            </div>
          </div>
        )}
        <RunStatus run={loader.run} err={loader.err} />
        {!editable && <div className="note blue" style={{ fontSize: 12 }}>Read-only: you need Modifier (or above) on this table and it must be unlocked.</div>}
        {err && <div className="error">{err}</div>}
      </div>
    </Dialog>
  )
}
