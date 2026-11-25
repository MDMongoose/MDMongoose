// Backend calls of the main screen, and the fetch helper the features' api.js files use.

export const PAGE = 40
let asGroup = null
export function setViewAsGroup(g) { asGroup = g || null }
export const viewAsGroup = () => asGroup || ''

// Where each node lives (its path_key), learnt from the hierarchy responses: sent with /api/open so the
// server reads the node and its ancestors in one statement.
const keys = new Map()
function learnKeys(v, depth = 0) {
  if (!v || typeof v !== 'object' || depth > 6) return
  if (Array.isArray(v)) { for (const x of v) learnKeys(x, depth + 1); return }
  if (typeof v.uid === 'string' && typeof v.k === 'string') keys.set(v.uid, v.k)
  for (const x of Object.values(v)) if (x && typeof x === 'object') learnKeys(x, depth + 1)
}
export const keyOf = (uid) => keys.get(uid)

export async function call(method, url, body) {
  const sep = url.includes('?') ? '&' : '?'
  const viewAs = asGroup && ((method === 'GET' && (url.startsWith('/api/tree') || url.startsWith('/api/open') || url.startsWith('/api/prefetch') || /^\/api\/nodes\/[^/?]+(\?|$)/.test(url) || /^\/api\/nodes\/[^/?]+\/children\?/.test(url)))
    || (method === 'POST' && /\/children-table\/query$/.test(url)))   // the grid shows what the group may do
  const full = viewAs ? `${url}${sep}as_group=${encodeURIComponent(asGroup)}` : url
  const res = await fetch(full, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`
    try { const j = await res.json(); msg = j.error || (typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)) || msg } catch { /* not json */ }
    const err = new Error(msg)
    err.status = res.status
    throw err
  }
  const data = await res.json()
  if (method === 'GET' && /^\/api\/(tree|open|prefetch|nodes\/[^/?]+\/children)/.test(url)) learnKeys(data)
  return data
}

export const qs = (o) => Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== '')
  .map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&')

export const api = {
  me: () => call('GET', '/api/me'),
  meta: () => call('GET', '/api/meta'),
  tree: (uid) => call('GET', `/api/tree/${uid}${keys.has(uid) ? `?k=${encodeURIComponent(keys.get(uid))}` : ''}`),
  open: (uid) => call('GET', `/api/open/${uid}${keys.has(uid) ? `?k=${encodeURIComponent(keys.get(uid))}` : ''}`),
  prefetch: (levels = 5) => call('GET', `/api/prefetch?levels=${levels}&per=${PAGE}`),
  node: (uid) => call('GET', `/api/nodes/${uid}`),
  nodeViews: (uids) => call('POST', `/api/node-views${asGroup ? `?as_group=${encodeURIComponent(asGroup)}` : ''}`, { uids }),
  search: (q, kind = '', under = '', limit) => call('GET', `/api/search?${qs({ q, kind, under, limit })}`),
  options: (ptype, q = '', offset, limit) => call('GET', `/api/options?${qs({ ptype, q, offset, limit })}`),
  edit: (uid, changes, expected, user_defined) => call('PATCH', `/api/nodes/${uid}`, { changes, expected, user_defined }),
  lock: (uid) => call('POST', `/api/nodes/${uid}/lock`),
  move: (uid, newParent) => call('POST', `/api/nodes/${uid}/move`, { new_parent: newParent }),
  validParents: (uid, q = '', offset, limit) => call('GET', `/api/nodes/${uid}/valid-parents?${qs({ q, offset, limit })}`),
  childrenPage: (uid, offset = 0, q = '', limit = PAGE) => call('GET', `/api/nodes/${uid}/children?${qs({ offset, limit, q })}`),
  resolvePermissions: (entries) => call('POST', '/api/permissions/resolve', { entries }),
  runAction: (uid, actionUid, options) => call('POST', `/api/nodes/${uid}/actions/${actionUid}/run`, { options }),
  run: (runId) => call('GET', `/api/runs/${runId}`),
  saveNodeType: (uid, body) => call('PUT', `/api/nodetypes/${uid}`, body),
}
