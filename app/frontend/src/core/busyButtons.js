// Every action button shows a spinner and cannot be clicked again while the backend works on its click.
// A click on a button marks it as the source of the requests it starts (the click handler, and the
// state changes and effects it triggers, run within a few milliseconds); the button is busy until all
// of them are answered. Buttons that start no request (Cancel, Close, tabs) never change.
const ACTION = 'button.btn, button.tb, button.ib, button.lnk'
const SKIP = '.sel, .sel-pop, .crumbs, .nsearch-btn, [data-nobusy]'
const WINDOW_MS = 60

let source = null
let clearTimer = null
const pending = new WeakMap()

function busy(b, on) {
  const n = (pending.get(b) || 0) + (on ? 1 : -1)
  pending.set(b, Math.max(0, n))
  if (on && n === 1) { b.classList.add('is-busy'); b.setAttribute('aria-busy', 'true') }
  if (!on && n <= 0) { b.classList.remove('is-busy'); b.removeAttribute('aria-busy') }
}

export function installBusyButtons() {
  if (window.__busyButtons) return
  window.__busyButtons = true
  document.addEventListener('click', (e) => {
    const b = e.target.closest?.('button')
    if (b && b.classList.contains('is-busy')) { e.preventDefault(); e.stopImmediatePropagation(); return }
    source = b && b.matches(ACTION) && !b.closest(SKIP) ? b : null
    clearTimeout(clearTimer)
    clearTimer = setTimeout(() => { source = null }, WINDOW_MS)
  }, true)
  document.addEventListener('keydown', (e) => {   // Enter / Space on a busy button does nothing either
    if ((e.key === 'Enter' || e.key === ' ') && e.target?.classList?.contains('is-busy')) { e.preventDefault(); e.stopImmediatePropagation() }
  }, true)
  const fetch0 = window.fetch.bind(window)
  window.fetch = (...args) => {
    const b = source
    const p = fetch0(...args)
    if (b && b.isConnected) {
      busy(b, true)
      // the handler often sends the next request when this one is answered (save, then reload): keep the
      // button as the source for a moment, and release it only after that moment
      p.finally(() => {
        source = b
        clearTimeout(clearTimer)
        clearTimer = setTimeout(() => { source = null }, WINDOW_MS)
        setTimeout(() => busy(b, false), WINDOW_MS)
      }).catch(() => {})
    }
    return p
  }
}
