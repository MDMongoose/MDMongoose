// Golden record setup endpoints (saving uses the Match Rule and Survivorship Rule endpoints).
import { call } from '../../core/api.js'

export const goldenSetupApi = {
  setup: (uid) => call('GET', `/api/golden-setup/${uid}`),
  assign: (uid, master) => call('POST', `/api/golden-setup/${uid}/assign`, { master }),
  processing: (uid, mode) => call('POST', `/api/golden-setup/${uid}/processing`, { mode }),
}
