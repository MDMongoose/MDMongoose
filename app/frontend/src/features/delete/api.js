// Delete endpoints.
import { call } from '../../core/api.js'

export const deleteApi = {
  remove: (uid, mode) => call('DELETE', `/api/nodes/${uid}?mode=${mode || 'tree'}`),
  deletePreview: (uid) => call('GET', `/api/nodes/${uid}/delete-preview`),
  deleteChildren: (uid) => call('POST', `/api/nodes/${uid}/delete-children`),
}
