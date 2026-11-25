import RecordMatchingDialog from './RecordMatchingDialog.jsx'
import ReviewDialog from './ReviewDialog.jsx'

// Matching: the steward review queue and fixes on single records. The Match Rule is edited in
// Golden record setup (golden_setup) and run in Golden records run (golden_run).
export default {
  name: 'matching',
  actions: ['review_matches', 'record_matching', 'golden_matching'],
  render: ({ node, action, ctx }) => {
    const closeReload = () => { ctx.close(); ctx.reload() }
    if (action?.ui === 'review_matches') return <ReviewDialog node={node} onClose={closeReload} />
    return <RecordMatchingDialog node={node} onClose={closeReload} onGo={(uid) => { ctx.close(); ctx.go(uid) }} />
  },
}
