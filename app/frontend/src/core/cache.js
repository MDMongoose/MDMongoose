// In-browser cache of everything the UI has loaded; cached views show at once and refresh in the background.

const store = new Map()      // key -> { data, at }
const inflight = new Map()   // key -> Promise
const MAX = 12000
let queue = []
let running = 0
const PREFETCH_PARALLEL = 2

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b)

function put(key, data) {
  store.delete(key)
  store.set(key, { data, at: Date.now() })
  if (store.size > MAX) store.delete(store.keys().next().value)
}

export function peek(key) { return store.get(key)?.data }

export function load(key, fetcher) {
  if (inflight.has(key)) return inflight.get(key)
  const p = fetcher().then((d) => { put(key, d); return d }).finally(() => inflight.delete(key))
  inflight.set(key, p)
  return p
}

export function swr(key, fetcher, onFresh, onError) {
  const cached = peek(key)
  const wasRunning = inflight.has(key)
  let p = load(key, fetcher)
  if (wasRunning) p = p.catch(() => load(key, fetcher))
  p.then((d) => { if (cached === undefined || !same(cached, d)) onFresh(d) })
    .catch((e) => { if (cached === undefined && onError) onError(e) })
  return cached
}

// One request that fills two keys (the hierarchy and the property page of a node: /api/open). A key already
// loading keeps its own request.
export function loadPair(keyA, keyB, fetchBoth, pick = (r) => [r.tree, r.node]) {
  if (inflight.has(keyA) && inflight.has(keyB)) return Promise.all([inflight.get(keyA), inflight.get(keyB)])
  const both = fetchBoth()
  const one = (key, i) => {
    if (inflight.has(key)) return inflight.get(key)
    const p = both.then((r) => { const d = pick(r)[i]; put(key, d); return d })
      .finally(() => { if (inflight.get(key) === p) inflight.delete(key) })
    p.catch(() => {})
    inflight.set(key, p)
    return p
  }
  return Promise.all([one(keyA, 0), one(keyB, 1)])
}

// a pair load in the background (hover): queued like prefetch, at most PREFETCH_PARALLEL at a time
export function prefetchPair(keyA, keyB, fetchBoth) {
  if ((store.has(keyA) && store.has(keyB)) || inflight.has(keyA) || inflight.has(keyB) || queue.some((q) => q.key === keyA)) return
  queue.unshift({ key: keyA, run: () => loadPair(keyA, keyB, fetchBoth) })
  queue = queue.slice(0, 20)
  pump()
}

export function prefetch(key, fetcher) {
  if (store.has(key) || inflight.has(key) || queue.some((q) => q.key === key)) return
  queue.unshift({ key, fetcher })
  queue = queue.slice(0, 20)
  pump()
}

function pump() {
  while (running < PREFETCH_PARALLEL && queue.length) {
    const { key, fetcher, run } = queue.shift()
    if (inflight.has(key) || (!run && store.has(key))) continue
    running++
    ;(run ? run() : load(key, fetcher)).catch(() => {}).finally(() => { running--; pump() })
  }
}

export function preloadMany(keys, fetchAll, keyOf) {
  const want = keys.filter((k) => !store.has(k) && !inflight.has(k))
  if (!want.length) return
  const all = fetchAll(want).then((res) => { for (const [id, data] of Object.entries(res || {})) put(keyOf(id), data); return res })
  for (const k of want) {
    const p = all.then(() => { if (!store.has(k)) throw new Error('not preloaded'); return store.get(k).data })
      .finally(() => { if (inflight.get(k) === p) inflight.delete(k) })
    p.catch(() => {})
    inflight.set(k, p)
  }
}

export function clearCache() { store.clear(); queue = [] }

export function seed(key, data) { if (!store.has(key)) put(key, data) }
