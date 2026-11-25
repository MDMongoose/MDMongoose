// Browse endpoints.
import { call, qs } from '../../core/api.js'

export const browseApi = {
  manageCount: (body) => call('POST', '/api/manage/count', body),
  manageParents: (body, q = '') => call('POST', `/api/manage/parents?${qs({ q })}`, body),
  managePlan: (op, body) => call('POST', `/api/manage/${op}/plan`, body),
  manageApply: (op, body) => call('POST', `/api/manage/${op}`, body),
}
