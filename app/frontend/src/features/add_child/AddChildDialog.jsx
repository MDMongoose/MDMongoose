import { useEffect, useMemo, useState } from 'react'
import { api } from '../../core/api.js'
import { ICON_NAMES, Icon } from '../../components/icons.jsx'
import { Dialog, Field, useBusy } from '../../components/Dialogs.jsx'
import { addChildApi } from './api.js'
import { HELP } from '../../core/docs.js'
import { Select } from '../../components/Select.jsx'

// Add Child: a child node with the union form of its types, or a new NodeType.
const PT_BASES = ['UID', 'Number', 'Float', 'String', 'Timestamp', 'Link', 'Enum', 'Array', 'Expression', 'Icon', 'Boolean']
const PARAM_PH = { Link: 'target NodeType, e.g. Customer', Enum: 'enum root, e.g. Countries', Array: 'item NodeType, e.g. Contact — or String', Expression: "Street || ', ' || City" }

function FieldInput({ f, value, onChange }) {
  const base = f.base
  const [label, setLabel] = useState('')
  if (base === 'Boolean') return <label className="row" style={{ height: 34 }}><input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} />{f.name}</label>
  if (base === 'Enum') return <Select className="in" value={value || ''} onChange={(e) => { onChange(e.target.value); setLabel(e.target.label) }} placeholder="—"
    selectedLabel={label} searchPlaceholder="Search name or UID…" fetchOptions={async (q, offset, limit) => {
      const got = (await api.options(f.ptype, q, offset, limit)).map((o) => ({ value: o.uid, label: `${o.uid} — ${o.name}` }))
      return offset || q ? got : [{ value: '', label: '—' }, ...got]
    }} />
  if (base === 'Icon') return <Select className="in" value={value || ''} onChange={(e) => onChange(e.target.value)}><option value="">inherit from type</option>{ICON_NAMES.map((i) => <option key={i}>{i}</option>)}</Select>
  if (base === 'Expression') return <textarea className="in mono" rows={2} value={value || ''} onChange={(e) => onChange(e.target.value)} />
  return <input className={`in ${base === 'Number' || base === 'Float' ? 'mono' : ''}`} value={value || ''} onChange={(e) => onChange(e.target.value)}
    placeholder={base === 'Array' ? 'comma-separated names or UIDs' : base === 'Link' ? 'name or UID' : f.name === 'Max Child Depth' ? 'empty = unlimited' : ''} />
}

export default function AddChildDialog({ node, meta, onClose, onDone }) {
  const allowed = node.allowed_child_types || []
  const isTypesFolder = meta && node.uid === meta.model_node_types
  const [mode, setMode] = useState(isTypesFolder ? 'type' : 'node')
  const [types, setTypes] = useState(allowed.length ? [allowed[0].uid] : [])
  const [vals, setVals] = useState({})
  const [defsByType, setDefsByType] = useState({})
  const [more, setMore] = useState(false)
  const [tname, setTname] = useState('')
  const [ticon, setTicon] = useState('box')
  const [defs, setDefs] = useState([{ name: '', base: 'String', param: '', group: 'General', priority: 10 }])
  const [acts, setActs] = useState([])
  const [na, setNa] = useState({ name: '', handler: '' })
  const [busy, err, run] = useBusy()

  const typeChoices = (meta?.node_types || []).filter((t) => t.uid !== 'MDM-1E' && (!allowed.length || allowed.some((a) => a.uid === t.uid)))
  const allTypes = meta?.node_types || []

  useEffect(() => {
    types.filter((t) => !defsByType[t]).forEach((t) => api.node(t).then((n) => setDefsByType((d) => ({ ...d, [t]: n.definition?.property_defs || [] }))).catch(() => {}))
  }, [types])
  const fields = useMemo(() => {
    const out = [{ name: 'Name', base: 'String', ptype: 'String', from: '' }, { name: 'Icon', base: 'Icon', ptype: 'Icon', from: '' }, { name: 'Max Child Depth', base: 'Number', ptype: 'Number', from: '' }]
    const seen = new Set(['Name', 'Icon', 'Max Child Depth', 'Types', 'Parent', 'UID', 'Origin', 'Last Modified', 'Locked'])
    types.forEach((t) => (defsByType[t] || []).forEach((d) => {
      if (seen.has(d.name)) return
      seen.add(d.name)
      out.push({ name: d.name, base: d.base, ptype: d.ptype, from: 'from ' + (allTypes.find((x) => x.uid === t)?.name || t) })
    }))
    return out
  }, [types, defsByType])
  // the Node properties every node has (Icon, Max Child Depth) are behind "More properties"; Name and the
  // properties of the chosen types are shown
  const NODE_FIELDS = ['Icon', 'Max Child Depth']
  const shownFields = fields.filter((f) => more || !NODE_FIELDS.includes(f.name) || (vals[f.name] ?? '') !== '')
  const hidden = fields.length - shownFields.length

  const createNode = () => run(async () => {
    const { Name, ...rest } = vals
    const values = Object.fromEntries(Object.entries(rest).filter(([, v]) => v !== '' && v !== undefined))
    const r = await addChildApi.addChild(node.uid, Name || '', types.length ? types : ['Folder'], values)
    onDone(r.uid, `Added ${Name || 'the child'} under ${node.name}`)
  })
  const createType = () => run(async () => {
    const r = await addChildApi.createNodeType({ parent_uid: isTypesFolder ? node.uid : meta?.model_node_types, name: tname, default_icon: ticon,
      property_defs: defs.filter((d) => d.name.trim()), actions: acts, new_action: na.name ? { ...na, group: 'Self' } : null })
    onDone(r.uid, `NodeType ${tname} created (${r.uid}) — add it to a node's Types to use it`)
  })

  return (
    <Dialog title={mode === 'node' ? 'Add Child' : 'New NodeType'} onClose={onClose} busy={busy} busyLabel="Saving…"
      help={mode === 'node' ? HELP.add_child : HELP.node_type}
      footer={<div className="r"><button className="btn" onClick={onClose}>Cancel</button>
        {mode === 'node' ? <button className="btn pri" disabled={busy || !(vals.Name || '').trim()} onClick={createNode}>Add child</button>
          : <button className="btn pri" disabled={busy || !tname.trim()} onClick={createType}>Create NodeType</button>}</div>}>
      <div style={{ padding: '0 24px' }}>
        <div role="tablist" className="tabs">
          <button role="tab" aria-selected={mode === 'node'} className={mode === 'node' ? 'on' : ''} onClick={() => setMode('node')}>Child node</button>
          <button role="tab" aria-selected={mode === 'type'} className={mode === 'type' ? 'on' : ''} onClick={() => setMode('type')}>NodeType</button>
        </div>
      </div>
      <div className="dbody">
        {mode === 'node' ? (
          <>
            <div style={{ fontSize: 13, color: 'var(--ink-2)' }}>Parent: <strong>{node.path.map((p) => p.name).join(' / ')}</strong> <span className="mono faint">{node.uid}</span></div>
            <div>
              <span className="lbl">Types <span className="ptag">Array(NodeType)</span></span>
              <div className="row" style={{ gap: 6 }}>
                <span className="chip" style={{ background: 'var(--bg)', paddingRight: 8 }}><span className="mono faint">MDM-1E</span>Node</span>
                {types.map((t) => {
                  const tt = allTypes.find((x) => x.uid === t)
                  return <span key={t} className="chip"><span className="mono faint">{t}</span>{tt?.name || t}<button className="ib ghost" onClick={() => setTypes(types.filter((x) => x !== t))} aria-label={`Remove type ${tt?.name}`}>×</button></span>
                })}
                <Select className="in sm dashed" aria-label="Add type" value="" searchPlaceholder="Search types…" onChange={(e) => e.target.value && setTypes([...types, e.target.value])}>
                  <option value="">+ Add type…</option>
                  {typeChoices.filter((t) => !types.includes(t.uid)).map((t) => <option key={t.uid} value={t.uid}>{t.uid} — {t.name}</option>)}
                </Select>
              </div>
              <div className="faint" style={{ fontSize: 12, marginTop: 6 }}>The form below is the union of all selected types’ properties.{allowed.length > 0 && ` Allowed here: ${allowed.map((a) => a.name).join(', ')}.`}</div>
            </div>
            <div className="grid2">
              {shownFields.map((f) => (
                <div key={f.name} className={f.base === 'Expression' || f.base === 'Array' ? 'span2' : ''}>
                  <label className="lbl">{f.name} <span className="ptag">{f.ptype}</span><span className="hint" style={{ fontSize: 11 }}>{f.from}</span></label>
                  <FieldInput f={f} value={vals[f.name]} onChange={(v) => setVals((x) => ({ ...x, [f.name]: v }))} />
                </div>
              ))}
            </div>
            {(hidden > 0 || more) && <button className="lnk" style={{ alignSelf: 'flex-start', fontSize: 12.5 }} onClick={() => setMore(!more)}>
              {more ? 'Fewer properties' : `More properties (${hidden}): ${NODE_FIELDS.join(', ')}`}</button>}
          </>
        ) : (
          <>
            <div className="note">A NodeType works like an interface: it adds properties and actions to every node that lists it in <span className="mono">Types</span>. Custom types live in Model / NodeTypes.</div>
            <div className="grid2">
              <Field label="Name" ptag="String" htmlFor="tname"><input id="tname" className="in" placeholder="e.g. Address" value={tname} onChange={(e) => setTname(e.target.value)} /></Field>
              <Field label="Default Icon" ptag="Icon" htmlFor="ticon">
                <div className="row" style={{ flexWrap: 'nowrap' }}><Icon name={ticon} size={18} /><Select id="ticon" className="in" value={ticon} onChange={(e) => setTicon(e.target.value)}>{ICON_NAMES.map((i) => <option key={i}>{i}</option>)}</Select></div>
              </Field>
            </div>
            <div className="stack">
              <div className="row" style={{ justifyContent: 'space-between' }}><span className="sec">Properties</span>
                <button className="lnk" onClick={() => setDefs([...defs, { name: '', base: 'String', param: '', group: defs.at(-1)?.group || 'General', priority: defs.at(-1)?.priority ?? 10 }])}>+ Add property</button></div>
              <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1.1fr 1.4fr 1fr 64px 32px', gap: 8, fontSize: 11, fontWeight: 600, color: 'var(--ink-3)' }}><span>NAME</span><span>PROPERTYTYPE</span><span>PARAMETER</span><span>GROUP</span><span>PRIO</span><span /></div>
              {defs.map((d, i) => {
                const set = (p) => setDefs(defs.map((x, j) => (j === i ? { ...x, ...p } : x)))
                return (
                  <div key={i} style={{ display: 'grid', gridTemplateColumns: '1.4fr 1.1fr 1.4fr 1fr 64px 32px', gap: 8, alignItems: 'center' }}>
                    <input className="in" aria-label="Property name" value={d.name} onChange={(e) => set({ name: e.target.value })} />
                    <Select className="in" aria-label="PropertyType" value={d.base} onChange={(e) => set({ base: e.target.value, param: '' })}>{PT_BASES.map((p) => <option key={p}>{p}</option>)}</Select>
                    <input className="in mono" aria-label="Parameter" value={d.param} placeholder={PARAM_PH[d.base] || '—'} disabled={!PARAM_PH[d.base]} onChange={(e) => set({ param: e.target.value })} />
                    <input className="in" aria-label="Group" value={d.group} onChange={(e) => set({ group: e.target.value })} />
                    <input className="in mono" aria-label="Priority" value={d.priority} onChange={(e) => set({ priority: e.target.value })} />
                    <button className="ib" style={{ width: 32, height: 32 }} onClick={() => setDefs(defs.filter((_, j) => j !== i))} aria-label="Remove property">×</button>
                  </div>
                )
              })}
            </div>
            <div className="stack">
              <span className="sec">Actions <span className="ptag">Array(Action)</span></span>
              <div className="row" style={{ gap: '8px 16px' }}>
                {(meta?.actions || []).map((a) => (
                  <label key={a.uid} className="row" style={{ gap: 6, fontSize: 13 }} title={a.handler}>
                    <input type="checkbox" checked={acts.includes(a.uid)} onChange={(e) => setActs(e.target.checked ? [...acts, a.uid] : acts.filter((x) => x !== a.uid))} />
                    {a.name} <span className="mono faint" style={{ fontSize: 11 }}>{a.handler}</span>
                  </label>
                ))}
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.6fr', gap: 8, borderTop: '1px dashed var(--line)', paddingTop: 10 }}>
                <Field label="New action" htmlFor="naname"><input id="naname" className="in" placeholder="e.g. Validate VAT" value={na.name} onChange={(e) => setNa({ ...na, name: e.target.value })} /></Field>
                <Field label="Python handler" hint="in mdm_custom, runs as a job" htmlFor="nahandler"><input id="nahandler" className="in mono" placeholder="mdm_custom.actions.validate_vat" value={na.handler} onChange={(e) => setNa({ ...na, handler: e.target.value })} /></Field>
              </div>
              <div className="faint" style={{ fontSize: 12 }}>The new action is created in Model / Actions and assigned to this NodeType when you click Create NodeType.</div>
            </div>
          </>
        )}
        {err && <div className="error">{err}</div>}
      </div>
    </Dialog>
  )
}
