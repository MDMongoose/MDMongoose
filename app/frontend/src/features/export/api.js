// Export endpoints.
import { call, qs } from '../../core/api.js'

export const exportApi = {
  exportPreview: (uid, o) => call('GET', `/api/nodes/${uid}/export/preview?${qs(o)}`),
  exportUrl: (uid, o) => `/api/nodes/${uid}/export?${qs(o)}`,
}
