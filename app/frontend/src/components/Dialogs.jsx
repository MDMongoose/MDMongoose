import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../core/api.js'
import { Icon } from './icons.jsx'
import { HELP, docUrl } from '../core/docs.js'

// Locking loading layer: covers its (position: relative) container and blocks input until data arrives.
export function Busy({ show, label = 'Loading…' }) {
  if (!show) return null
  return (
    <div className="busy" role="status" aria-live="polite" aria-busy="true"
      onMouseDown={(e) => { e.preventDefault(); e.stopPropagation() }} onKeyDown={(e) => e.preventDefault()}>
      <div className="busy-box"><span className="spin" aria-hidden="true" />{label}</div>
    </div>
  )
}

const openDialogs = []
function lockBackground(on) {
  const root = document.getElementById('root')
  if (root) { if (on) root.setAttribute('inert', '') ; else root.removeAttribute('inert') }
  document.body.style.overflow = on ? 'hidden' : ''
}
const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

export function Dialog({ title, sub, onClose, children, footer, size = '', labelledBy = 'dlgt', role = 'dialog', busy = false, busyLabel, help }) {
  const box = useRef()
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  useEffect(() => {
    const me = {}
    openDialogs.push(me)
    if (openDialogs.length === 1) lockBackground(true)
    const before = document.activeElement
    setTimeout(() => {
      const el = box.current
      if (!el || el.contains(document.activeElement)) return
      ;(el.querySelector('[data-autofocus]') || el).focus({ preventScroll: true })
    }, 0)
    const k = (e) => {
      if (openDialogs[openDialogs.length - 1] !== me) return
      if (e.key === 'Escape' && !e.defaultPrevented && !['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target?.tagName)) closeRef.current()
      if (e.key === 'Tab' && box.current) {
        const f = [...box.current.querySelectorAll(FOCUSABLE)].filter((x) => x.offsetParent !== null)
        if (!f.length) return
        const first = f[0], last = f[f.length - 1]
        if (e.shiftKey && (document.activeElement === first || document.activeElement === box.current)) { last.focus(); e.preventDefault() }
        else if (!e.shiftKey && document.activeElement === last) { first.focus(); e.preventDefault() }
      }
    }
    window.addEventListener('keydown', k)
    return () => {
      window.removeEventListener('keydown', k)
      openDialogs.splice(openDialogs.indexOf(me), 1)
      if (!openDialogs.length) lockBackground(false)
      if (before && before.focus && document.contains(before)) before.focus({ preventScroll: true })
    }
  }, [])
  return createPortal(
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`dialog ${size}`} role={role} aria-modal="true" aria-labelledby={labelledBy} ref={box} tabIndex={-1}>
        <div className="dhead">
          <div style={{ minWidth: 0 }}>
            <h1 id={labelledBy}>{title}</h1>
            {sub && <div className="sub">{sub}</div>}
          </div>
          <div className="dacts">
            {help && <a className="help" href={docUrl(help)} target="_blank" rel="noreferrer" aria-label="Help: open the documentation"
              title="Open the documentation of this dialog">?</a>}
            <button className="x" onClick={onClose} aria-label="Close"><Icon name="close" size={16} /></button>
          </div>
        </div>
        {children}
        {footer && <div className="dfoot">{footer}</div>}
        <Busy show={busy} label={busyLabel} />
      </div>
    </div>,
    document.body,
  )
}

export function useBusy() {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const run = async (fn) => { setBusy(true); setErr(null); try { await fn() } catch (e) { setErr(e.message) } setBusy(false) }
  return [busy, err, run, setErr]
}

export function useRun(node, action) {
  const [run, setRun] = useState(null)
  const [err, setErr] = useState(null)
  const timer = useRef()
  const start = async (options) => {
    setErr(null)
    try { setRun(await api.runAction(node.uid, action.uid, options)) } catch (e) { setErr(e.message) }
  }
  useEffect(() => {
    if (!run || ['SUCCESS', 'FAILED', 'CANCELED'].includes(run.state)) return
    timer.current = setTimeout(() => api.run(run.run_id).then(setRun).catch((e) => setErr(e.message)), 2000)
    return () => clearTimeout(timer.current)
  }, [run])
  const done = run && ['SUCCESS', 'FAILED', 'CANCELED'].includes(run.state)
  return { run, err, start, done, running: run && !done }
}

export function RunStatus({ run, err }) {
  if (err) return <div className="runbar fail">{err}</div>
  if (!run) return null
  const ok = run.state === 'SUCCESS', fail = ['FAILED', 'CANCELED'].includes(run.state)
  return (
    <div className={`runbar ${ok ? 'ok' : fail ? 'fail' : ''}`}>
      {!ok && !fail && <span className="spin" />}
      <strong>{run.state}</strong>
      <span className="mono" style={{ fontSize: 11.5, whiteSpace: 'pre-wrap', flex: 1 }}>{run.message || (ok || fail ? '' : 'serverless job starting — about a minute on Free Edition')}</span>
      {run.url && <a href={run.url} target="_blank" rel="noreferrer">Open run</a>}
    </div>
  )
}

export function ConfirmDialog({ title, text, confirm = 'OK', danger, onClose, onConfirm, help }) {
  const [busy, err, run] = useBusy()
  return (
    <Dialog title={title} onClose={onClose} size="sm" role="alertdialog" busy={busy} busyLabel="Working…" help={help}
      footer={<div className="r"><button className="btn" onClick={onClose}>Cancel</button>
        <button className={`btn ${danger ? 'dng' : 'pri'}`} disabled={busy} onClick={() => run(onConfirm)}>{confirm}</button></div>}>
      <div className="dbody"><div>{text}</div>{err && <div className="error">{err}</div>}</div>
    </Dialog>
  )
}

export function RunDialog({ node, action, onClose }) {
  const { run, err, start, done, running } = useRun(node, action)
  const what = action.ui === 'load_children'
    ? 'Refreshes only this table in the load pipeline (refresh selection), then merges changed records into MDMNodes by record hash. Values marked UserDefined are kept. If “Run golden-record build after each load” is set, the golden records that use this table are refreshed too.'
    : `Runs “${action.name}” on ${node.name} as a Databricks job.`
  return (
    <Dialog title={`${action.name}`} sub={<>{node.name} <span className="mono">{node.uid}</span></>} onClose={onClose} size="sm"
      help={action.ui === 'load_children' ? HELP.load_children : HELP.action}
      footer={<div className="r">{!run ? <><button className="btn" onClick={onClose}>Cancel</button><button className="btn pri" onClick={() => start()}><Icon name="bolt" size={13} />Run</button></>
        : <button className="btn pri" onClick={onClose} disabled={running && !err}>{done ? 'Close' : 'Running…'}</button>}</div>}>
      <div className="dbody">
        <div style={{ fontSize: 13, color: 'var(--ink-2)' }}>{what}</div>
        <RunStatus run={run} err={err} />
      </div>
    </Dialog>
  )
}

export function Field({ label, ptag, hint, children, htmlFor, style }) {
  return (
    <div style={style}>
      <label className="lbl" htmlFor={htmlFor}>{label}{ptag && <span className="ptag">{ptag}</span>}{hint && <span className="hint">{hint}</span>}</label>
      {children}
    </div>
  )
}
