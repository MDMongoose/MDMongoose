// Add Child Link endpoints.
import { call } from '../../core/api.js'

export const childLinkApi = {
  link: (uid, linkUid, linkType, position) => call('POST', `/api/nodes/${uid}/links`, { link_uid: linkUid, link_type: linkType, position }),
  unlink: (uid, linkUid) => call('DELETE', `/api/nodes/${uid}/links/${linkUid}`),
}
