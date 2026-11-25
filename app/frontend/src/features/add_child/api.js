// Add Child endpoints.
import { call } from '../../core/api.js'

export const addChildApi = {
  addChild: (uid, name, types, values) => call('POST', `/api/nodes/${uid}/children`, { name, types, values }),
  createNodeType: (body) => call('POST', '/api/nodetypes', body),
}
