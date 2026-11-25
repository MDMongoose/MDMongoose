// Refresh Golden Records endpoints.
import { call, qs } from '../../core/api.js'

export const goldenRecordsApi = {
  goldenPlan: (uid, threshold, retire) => call('GET', `/api/nodes/${uid}/golden/plan?${qs({ threshold, retire })}`),
}
