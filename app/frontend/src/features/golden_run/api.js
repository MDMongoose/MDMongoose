// Golden records run endpoints; the run itself is followed with api.run(run_id).
import { call } from '../../core/api.js'

export const goldenRunApi = {
  view: (uid) => call('GET', `/api/golden-run/${uid}`),
  run: (uid, body) => call('POST', `/api/golden-run/${uid}/run`, body),
}
