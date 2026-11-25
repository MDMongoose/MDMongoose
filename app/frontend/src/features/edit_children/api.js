// Edit Children endpoints.
import { call, PAGE } from '../../core/api.js'

export const editChildrenApi = {
  gridQuery: (uid, body) => call('POST', `/api/nodes/${uid}/children-table/query`, { limit: PAGE, ...body }),
  gridValues: (uid, column, q = '', limit = PAGE) => call('POST', `/api/nodes/${uid}/children-table/values`, { column, q, limit }),
  childrenTable: (uid, levels = 1) => call('GET', `/api/nodes/${uid}/children-table?levels=${levels}`),
  saveChildren: (uid, changes, deletes) => call('POST', `/api/nodes/${uid}/children-table`, { changes, deletes }),
}
