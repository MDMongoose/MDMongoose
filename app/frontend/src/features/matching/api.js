// Matching endpoints.
import { call, qs } from '../../core/api.js'

export const matchingApi = {
  matchRule: (uid) => call('GET', `/api/match-rules/${uid}`),
  saveMatchRule: (uid, body) => call('PUT', `/api/match-rules/${uid}`, body),
  matchClean: (uid, body) => call('POST', `/api/match-rules/${uid}/clean-preview`, body),
  matchTestPair: (uid, body) => call('POST', `/api/match-rules/${uid}/test-pair`, body),
  matchBlocking: (uid, body) => call('POST', `/api/match-rules/${uid}/blocking`, body),
  matchActivate: (uid) => call('POST', `/api/match-rules/${uid}/activate-model`),
  matchRuleFor: (uid) => call('GET', `/api/matching/${uid}/rule`),
  matchReview: (uid) => call('GET', `/api/matching/${uid}/review`),
  matchDetails: (uid, a, b) => call('GET', `/api/matching/${uid}/details?${qs({ a, b })}`),
  matchRecord: (uid) => call('GET', `/api/matching/${uid}/record`),
  matchCandidates: (uid, q) => call('GET', `/api/matching/${uid}/candidates?${qs({ q })}`),
  matchDecide: (uid, body) => call('POST', `/api/matching/${uid}/decide`, body),
  matchSplit: (uid, body) => call('POST', `/api/matching/${uid}/split`, body || {}),
  matchMerge: (uid, body) => call('POST', `/api/matching/${uid}/merge`, body),
  matchLog: (uid, limit = 10) => call('GET', `/api/matching/${uid}/log?${qs({ limit })}`),
}
