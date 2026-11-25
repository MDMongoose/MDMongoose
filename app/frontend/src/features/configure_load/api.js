// Configure Load endpoints.
import { call, qs } from '../../core/api.js'

export const configureLoadApi = {
  sourceCdf: (table) => call('GET', `/api/source-cdf?${qs({ table })}`),
  loadConfig: (uid) => call('GET', `/api/tables/${uid}/load-config`),
  saveLoadConfig: (uid, body) => call('PUT', `/api/tables/${uid}/load-config`, body),
  loadPreview: (uid, body) => call('POST', `/api/tables/${uid}/preview`, body),
  sourceCatalog: (catalog, schema) => call('GET', `/api/source-catalog?${qs({ catalog, schema })}`),
}
