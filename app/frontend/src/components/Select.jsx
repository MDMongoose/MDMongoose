import { Children, Fragment, isValidElement, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

// The app's dropdown. Written like a <select> (value, onChange(e) with e.target.value, <option> children),
// but it opens a list with a search field on top that shows 40 entries and a "Show 40 more" link.
//   static list   the <option> children are searched in the browser
//   fetchOptions  (q, offset, limit) => Promise<[{ value, label, disabled?, title?, sub? }]>: the search and the
//                 paging go to the database; children are optional and only label the current value
export const STEP = 40

function textOf(n) {
  if (n == null || n === false || n === true) return ''
  if (typeof n === 'string' || typeof n === 'number') return String(n)
  if (Array.isArray(n)) return n.map(textOf).join('')
  if (isValidElement(n)) return textOf(n.props.children)
  return ''
}

function optionsOf(children, out = []) {
  Children.forEach(children, (c) => {
    if (!isValidElement(c)) return
    if (c.type === Fragment) return optionsOf(c.props.children, out)
    if (c.type === 'option') {
      const label = textOf(c.props.children)
      out.push({ value: c.props.value !== undefined ? String(c.props.value) : label, label, disabled: !!c.props.disabled, title: c.props.title })
    }
  })
  return out
}

export function Select({ value, onChange, onBlur, onKeyDown, children, fetchOptions, selectedLabel, className = 'in', style, disabled,
  id, autoFocus, placeholder, searchPlaceholder = 'Search…', emptyText, title, ...rest }) {
  const statics = useMemo(() => optionsOf(children), [children])
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [shown, setShown] = useState(STEP)
  const [remote, setRemote] = useState(null)   // { q, items, more }
  const [loading, setLoading] = useState(false)
  const [hi, setHi] = useState(0)
  const [pos, setPos] = useState(null)
  const btn = useRef()
  const pop = useRef()
  const list = useRef()
  const latest = useRef({})
  const picked = useRef(null)   // the label of the last picked value (a database list is gone once closed)
  latest.current = { onChange, onBlur, onKeyDown }

  const sval = value == null ? '' : String(value)
  const cur = statics.find((o) => o.value === sval) || remote?.items.find((o) => String(o.value) === sval)
    || (picked.current && picked.current.value === sval ? picked.current : null)
  const blank = statics.find((o) => o.value === '')
  const shownLabel = cur ? cur.label : (sval ? (selectedLabel || sval) : (placeholder || blank?.label || ''))

  // static list: filtered here; remote list: asked from the database (debounced), STEP at a time
  const ql = q.trim().toLowerCase()
  const filtered = useMemo(() => fetchOptions ? [] : statics.filter((o) => !ql || o.label.toLowerCase().includes(ql) || o.value.toLowerCase().includes(ql)),
    [statics, ql, fetchOptions])
  // the blank entry (placeholder / clear) is listed only when there is something to clear
  const items = (fetchOptions ? (remote?.items || []) : filtered.slice(0, shown)).filter((o) => sval !== '' || String(o.value) !== '')
  const more = fetchOptions ? !!remote?.more : filtered.length > shown

  const load = async (text, offset) => {
    setLoading(true)
    try {
      const got = await fetchOptions(text, offset, STEP + 1)
      setRemote((r) => ({ q: text, items: [...(offset && r ? r.items : []), ...got.slice(0, STEP)], more: got.length > STEP }))
    } catch { setRemote((r) => (offset && r ? r : { q: text, items: [], more: false })) }
    setLoading(false)
  }
  useEffect(() => {
    if (!open || !fetchOptions) return
    const t = setTimeout(() => load(q.trim(), 0), remote ? 200 : 0)
    return () => clearTimeout(t)
  }, [open, q])  // eslint-disable-line react-hooks/exhaustive-deps

  const place = () => {
    const r = btn.current?.getBoundingClientRect()
    if (!r) return
    const below = window.innerHeight - r.bottom, h = 330
    const up = below < h && r.top > below
    setPos({ left: Math.min(r.left, window.innerWidth - Math.max(r.width, 260) - 8), width: Math.max(r.width, 260),
      top: up ? undefined : r.bottom + 2, bottom: up ? window.innerHeight - r.top + 2 : undefined, maxH: Math.max(160, Math.min(h, (up ? r.top : below) - 12)) })
  }
  useLayoutEffect(() => { if (open) place() }, [open])
  useEffect(() => {
    if (!open) return
    const off = (e) => { if (!pop.current?.contains(e.target) && !btn.current?.contains(e.target)) close(true) }
    const move = (e) => { if (!pop.current?.contains(e.target)) place() }
    document.addEventListener('mousedown', off, true)
    window.addEventListener('scroll', move, true)
    window.addEventListener('resize', place)
    return () => { document.removeEventListener('mousedown', off, true); window.removeEventListener('scroll', move, true); window.removeEventListener('resize', place) }
  }, [open])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (autoFocus && !disabled) setOpen(true) }, [])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setHi(0) }, [q])
  useEffect(() => { list.current?.querySelector('[data-hi="1"]')?.scrollIntoView({ block: 'nearest' }) }, [hi])

  const close = (blur) => {
    setOpen(false); setQ(''); setShown(STEP)
    if (fetchOptions) setRemote(null)
    if (blur && latest.current.onBlur) setTimeout(() => latest.current.onBlur?.(), 0)
  }
  const pick = (o) => {
    if (!o || o.disabled) return
    const v = String(o.value)
    picked.current = { value: v, label: o.label }
    setOpen(false); setQ(''); setShown(STEP)
    if (fetchOptions) setRemote(null)
    if (v !== sval) latest.current.onChange?.({ target: { value: v, label: o.label }, currentTarget: { value: v } })
    btn.current?.focus({ preventScroll: true })
    if (latest.current.onBlur) setTimeout(() => latest.current.onBlur?.(), 0)
  }
  const keys = (e) => {
    const n = items.length
    if (e.key === 'ArrowDown') { e.preventDefault(); setHi((h) => Math.min(n - 1, h + 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setHi((h) => Math.max(0, h - 1)) }
    else if (e.key === 'Enter') { e.preventDefault(); pick(items[hi]) }
    else if (e.key === 'Escape') {
      e.preventDefault(); e.stopPropagation(); close(false); btn.current?.focus({ preventScroll: true })
      latest.current.onKeyDown?.({ key: 'Escape', preventDefault() {}, stopPropagation() {} })
    } else if (e.key === 'Tab') close(true)
  }
  const showMore = () => fetchOptions ? load(remote?.q ?? q.trim(), remote?.items.length || 0) : setShown((s) => s + STEP)

  return (
    <>
      <button type="button" ref={btn} id={id} className={`${className} sel`} style={style} disabled={disabled} title={title || shownLabel}
        aria-haspopup="listbox" aria-expanded={open} {...rest}
        onClick={() => (open ? close(false) : setOpen(true))}
        onKeyDown={(e) => {
          if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(e.key) && !open) { e.preventDefault(); setOpen(true) }
          else if (e.key === 'Escape' && !open) latest.current.onKeyDown?.(e)
        }}>
        <span className={`sel-text ${cur || sval ? '' : 'ph'}`}>{shownLabel || ' '}</span>
      </button>
      {open && pos && createPortal(
        <div className="sel-pop" ref={pop} style={{ left: pos.left, width: pos.width, top: pos.top, bottom: pos.bottom, maxHeight: pos.maxH }}
          onMouseDown={(e) => e.stopPropagation()}>
          <div className="sel-search">
            <input autoFocus value={q} placeholder={searchPlaceholder} aria-label="Search the list"
              onChange={(e) => setQ(e.target.value)} onKeyDown={keys} />
            {loading && <span className="spin sm" aria-label="Loading…" role="status" />}
          </div>
          <div className="sel-list" role="listbox" ref={list} aria-label={rest['aria-label'] || 'Options'}>
            {items.map((o, i) => (
              <button type="button" key={`${o.value}:${i}`} role="option" aria-selected={String(o.value) === sval} disabled={o.disabled}
                data-hi={i === hi ? '1' : undefined} className={`sel-opt ${String(o.value) === sval ? 'on' : ''} ${i === hi ? 'hi' : ''}`}
                title={o.title || o.label} onMouseEnter={() => setHi(i)} onClick={() => pick(o)}>
                <span className="sel-opt-text">{o.label || ' '}</span>{o.sub && <span className="sel-opt-sub">{o.sub}</span>}
              </button>
            ))}
            {!items.length && <div className="sel-none">{loading ? 'Loading…' : ql ? 'No match' : (emptyText || 'Nothing to choose')}</div>}
          </div>
          {more && <button type="button" className="sel-more lnk" onClick={showMore} disabled={loading}>Show {STEP} more</button>}
        </div>, document.body)}
    </>
  )
}
