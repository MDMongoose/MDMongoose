import { useEffect, useState } from 'react'
import { Icon } from '../../components/icons.jsx'
import { Dialog, Field } from '../../components/Dialogs.jsx'
import { exportApi } from './api.js'
import { Select } from '../../components/Select.jsx'
import { HELP } from '../../core/docs.js'

// Export: a branch as CSV or Excel, flattened into one row per node, with a live preview.
const LEVEL_BG = ['#FFFFFF', '#F5FBFA', '#FBF8FE', '#F7F9FE']

function Mini({ cells }) {
  const st = { a: { background: 'var(--accent-bg)', color: 'var(--accent-ink)', border: '1px solid var(--accent-line)' },
    c: { background: '#FFF4E5', color: 'var(--orange)', border: '1px dashed #D3A57A' }, e: { background: 'var(--bg)', color: 'var(--ink-5)', border: '1px solid var(--line)' } }
  return <span style={{ display: 'inline-flex', gap: 3, marginLeft: 8 }} aria-hidden="true">{cells.map(([t, k], i) =>
    <span key={i} style={{ ...st[k], fontSize: 10, width: 18, height: 16, borderRadius: 3, display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>{t}</span>)}</span>
}

export default function ExportDialog({ node, onClose }) {
  const today = new Date().toISOString().slice(0, 10)
  const [fmt, setFmt] = useState('xlsx')
  const [depth, setDepth] = useState(Math.min(2, Math.max(1, node.child_count ? 2 : 0)))
  const [bal, setBal] = useState(false)
  const [withProps, setWithProps] = useState(true)
  const [withUid, setWithUid] = useState(true)
  const [mark, setMark] = useState(false)
  const [fileName, setFileName] = useState(`${node.name.replace(/[^\w.-]+/g, '_')}_export_${today}`)
  const [delimiter, setDelimiter] = useState('comma')
  const [encoding, setEncoding] = useState('utf8bom')
  const [sheets, setSheets] = useState('one')
  const [header, setHeader] = useState('styled')
  const [pv, setPv] = useState(null)
  const [err, setErr] = useState(null)
  const opts = { format: fmt, depth, balanced: bal, with_props: withProps, with_uid: withUid, mark_balanced: mark, file_name: fileName, delimiter, encoding, sheets, header }
  useEffect(() => {
    const t = setTimeout(() => exportApi.exportPreview(node.uid, opts).then((x) => { setPv(x); setErr(null) }).catch((e) => setErr(e.message)), 150)
    return () => clearTimeout(t)
  }, [fmt, depth, bal, withProps, withUid, mark])
  const maxDepth = pv?.max_depth ?? 5
  const summary = pv ? `${pv.total_rows} row(s) · ${pv.levels} level(s) · ${pv.columns.length} column(s)${pv.clones ? ` · ${pv.clones} cloned` : ''}${pv.total_rows > pv.rows.length ? ` · showing ${pv.rows.length}` : ''}` : ''
  return (
    <Dialog title="Export" help={HELP.export} size="md" labelledBy="exT" onClose={onClose} busy={!pv && !err}
      sub={<>Node <strong style={{ color: 'var(--ink)' }}>{node.path.map((p) => p.name).slice(1).join(' / ') || node.name}</strong> <span className="mono">{node.uid}</span> · action of NodeType <strong style={{ color: 'var(--ink)' }}>Node</strong></>}
      footer={<>
        <span style={{ fontSize: 12, color: 'var(--ink-3)' }}>Runs with your permission: only nodes you can Read are exported.</span>
        <div className="r"><button className="btn" onClick={onClose}>Cancel</button>
          <a className="btn pri" href={exportApi.exportUrl(node.uid, opts)} download onClick={() => setTimeout(onClose, 400)}><Icon name="download" size={14} />Download {fmt}</a></div>
      </>}>
      <div className="dbody">
        <div className="row" style={{ alignItems: 'flex-start', gap: 20 }}>
          <div style={{ flex: '1 1 320px' }}>
            <span className="lbl">Format <span className="ptag">Enum(ExportFormats)</span></span>
            <div role="radiogroup" aria-label="Format" className="row" style={{ flexWrap: 'nowrap' }}>
              {[['csv', 'CSV', 'one file, UTF-8'], ['xlsx', 'Excel (xlsx)', 'sheet with frozen header']].map(([k, l, s]) => (
                <button key={k} role="radio" aria-checked={fmt === k} className={`card-radio ${fmt === k ? 'on' : ''}`} style={{ flex: 1 }} onClick={() => setFmt(k)}><strong>{l}</strong><span className="sub">{s}</span></button>
              ))}
            </div>
          </div>
          <div>
            <label className="lbl" htmlFor="depth">Child depth <span className="ptag">Number</span></label>
            <div className="row" style={{ flexWrap: 'nowrap' }}>
              <button className="ib" style={{ width: 34, height: 34 }} onClick={() => setDepth(Math.max(0, depth - 1))} aria-label="Decrease depth">−</button>
              <input id="depth" className="in mono" style={{ width: 64, textAlign: 'center' }} type="number" min={0} max={maxDepth} value={depth} onChange={(e) => setDepth(Math.max(0, Math.min(10, +e.target.value || 0)))} />
              <button className="ib" style={{ width: 34, height: 34 }} onClick={() => setDepth(Math.min(10, depth + 1))} aria-label="Increase depth">+</button>
            </div>
          </div>
        </div>
        <div>
          <span className="lbl">Balanced <span className="ptag">Enum(BalanceModes)</span><span className="hint">— how branches shorter than the deepest one are filled</span></span>
          <div role="radiogroup" aria-label="Balanced" className="row" style={{ flexWrap: 'nowrap', alignItems: 'stretch' }}>
            <button role="radio" aria-checked={!bal} className={`card-radio ${!bal ? 'on' : ''}`} style={{ flex: 1 }} onClick={() => setBal(false)}>
              <span className="row" style={{ gap: 0 }}><strong>None</strong><Mini cells={[['A', 'a'], ['B', 'a'], ['', 'e']]} /></span>
              <span className="sub">Ragged: when a branch is shorter than the deepest one, its deeper level columns stay empty.</span></button>
            <button role="radio" aria-checked={bal} className={`card-radio ${bal ? 'on' : ''}`} style={{ flex: 1 }} onClick={() => setBal(true)}>
              <span className="row" style={{ gap: 0 }}><strong>Balanced</strong><Mini cells={[['A', 'a'], ['B', 'a'], ['B', 'c']]} /></span>
              <span className="sub">Top-down: a node without children is cloned into the empty levels below it, so every row has a value on every level.</span></button>
          </div>
        </div>
        <div className="row" style={{ gap: '8px 22px', fontSize: 13 }}>
          <label className="row" style={{ gap: 8 }}><input type="checkbox" checked={withProps} onChange={() => setWithProps(!withProps)} />Include properties (union of the NodeTypes on each level, like Edit Children)</label>
          <label className="row" style={{ gap: 8 }}><input type="checkbox" checked={withUid} onChange={() => setWithUid(!withUid)} />Include UID columns</label>
          <label className="row" style={{ gap: 8 }}><input type="checkbox" checked={mark} onChange={() => setMark(!mark)} />Add “Balanced” flag column</label>
        </div>
        <div className="grid3">
          <Field label="File name" htmlFor="fname"><input id="fname" className="in mono" value={fileName} onChange={(e) => setFileName(e.target.value)} /></Field>
          {fmt === 'csv' ? (
            <>
              <Field label="Delimiter" htmlFor="delim"><Select id="delim" className="in" value={delimiter} onChange={(e) => setDelimiter(e.target.value)}><option value="comma">, (comma)</option><option value="semicolon">; (semicolon — Excel EU)</option><option value="tab">Tab</option></Select></Field>
              <Field label="Encoding" htmlFor="enc"><Select id="enc" className="in" value={encoding} onChange={(e) => setEncoding(e.target.value)}><option value="utf8bom">UTF-8 with BOM</option><option value="utf8">UTF-8</option></Select></Field>
            </>
          ) : (
            <>
              <Field label="Sheet" htmlFor="sheet"><Select id="sheet" className="in" value={sheets} onChange={(e) => setSheets(e.target.value)}><option value="one">One sheet</option><option value="per_level">One sheet per level</option></Select></Field>
              <Field label="Header" htmlFor="hdr"><Select id="hdr" className="in" value={header} onChange={(e) => setHeader(e.target.value)}><option value="styled">Bold, frozen, with filters</option><option value="plain">Plain</option></Select></Field>
            </>
          )}
        </div>
        <div className="stack">
          <div className="row" style={{ justifyContent: 'space-between' }}><h2 className="sec">Preview</h2><span style={{ fontSize: 12, color: 'var(--ink-2)' }}>{summary}</span></div>
          {err && <div className="error">{err}</div>}
          {pv && (
            <div className="box" style={{ maxHeight: 300 }}>
              <table style={{ borderCollapse: 'collapse', minWidth: '100%' }}>
                <thead>
                  <tr>{pv.groups.map((g, i) => <th key={i} colSpan={g.span} style={{ fontSize: 11, fontWeight: 600, color: 'var(--violet)', background: '#F4F2FB', textAlign: 'left', padding: '5px 8px', borderBottom: '1px solid var(--violet-line)', whiteSpace: 'nowrap', borderLeft: i > 0 ? '2px solid var(--accent-line)' : undefined, position: 'sticky', top: 0 }}>{g.label}</th>)}</tr>
                  <tr>{pv.columns.map((c, i) => <th key={i} title={c.defined_by ? 'defined by ' + c.defined_by.join(' · ') : c.ptype} style={{ fontSize: 11, fontWeight: 600, color: c.shared ? 'var(--accent-ink)' : 'var(--ink-3)', textAlign: 'left', padding: '6px 8px', background: 'var(--bg-2)', borderBottom: '1px solid var(--line)', whiteSpace: 'nowrap', borderLeft: i > 0 && pv.columns[i - 1].L !== c.L ? '2px solid var(--accent-line)' : undefined, position: 'sticky', top: 26 }}>{c.label}</th>)}</tr>
                </thead>
                <tbody>
                  {pv.rows.map((r, ri) => (
                    <tr key={ri}>{r.map((c, i) => {
                      const col = pv.columns[i]
                      const edge = i > 0 && pv.columns[i - 1].L !== col.L ? '2px solid var(--accent-line)' : undefined
                      const base = { fontSize: 12, padding: '6px 8px', borderBottom: '1px solid var(--line-2)', borderRight: '1px solid var(--bg-3)', whiteSpace: 'nowrap', borderLeft: edge }
                      const k = c.kind
                      const style = k === 'empty' ? { ...base, background: 'var(--bg)' }
                        : k === 'undefined' ? { ...base, background: 'repeating-linear-gradient(135deg, #F4F5F7 0 4px, #E4E7EB 4px 5px)' }
                          : k === 'clone' ? { ...base, background: '#FFF4E5', fontStyle: 'italic', color: 'var(--orange)' }
                            : k === 'flag' ? { ...base, fontFamily: 'var(--mono)', color: c.text === 'TRUE' ? 'var(--orange)' : 'var(--ink-3)' }
                              : { ...base, background: LEVEL_BG[(col.L || 0) % 4], color: k === 'repeated' ? 'var(--ink-4)' : col.f === 'uid' ? 'var(--ink-3)' : 'var(--ink)', fontFamily: col.f === 'uid' ? 'var(--mono)' : undefined }
                      const title = k === 'clone' ? 'cloned to balance the hierarchy' : k === 'repeated' ? 'parent repeated (exploded)' : k === 'undefined' ? 'not defined by that node’s Types — exported empty' : k === 'empty' ? 'empty — this branch has no node on this level' : ''
                      return <td key={i} style={style} title={title}>{c.text}</td>
                    })}</tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="row" style={{ gap: '6px 16px', fontSize: 11, color: 'var(--ink-3)' }}>
            <span className="row" style={{ gap: 5 }}><span style={{ width: 14, height: 12, background: '#FFF4E5', border: '1px dashed #D3A57A', borderRadius: 2 }} />cloned to balance (italic)</span>
            <span className="row" style={{ gap: 5 }}><span style={{ width: 14, height: 12, background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 2 }} />empty level (not balanced)</span>
            <span className="row" style={{ gap: 5 }}><span className="hatch" />not defined by that node’s Types (exported empty)</span>
            <span>Exploded like Edit Children: one row per deepest node; L0 (the node itself) and the parents repeat on each row.</span>
          </div>
        </div>
      </div>
    </Dialog>
  )
}
