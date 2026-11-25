// Survivorship rule endpoints.
import { call } from '../../core/api.js'

export const survivorshipApi = {
  rule: (uid) => call('GET', `/api/rules/${uid}`),
  saveRule: (uid, body) => call('PUT', `/api/rules/${uid}`, body),
  rulePreview: (uid, body) => call('POST', `/api/rules/${uid}/preview`, body),
  ruleSimulate: (uid, body) => call('POST', `/api/rules/${uid}/simulate`, body),
}
