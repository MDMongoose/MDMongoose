import BrowseDialog from './BrowseDialog.jsx'

// Browse: the whole tree like a file manager; move, clone, link or delete many subtrees at once.
// Opened from the Browse link of the hierarchy card, not by an Action node.
export default {
  name: 'browse',
  actions: [],
  render: ({ ctx }) => <BrowseDialog meta={ctx.meta} onClose={ctx.close} onChanged={ctx.reload} onGo={(uid) => { ctx.close(); ctx.go(uid) }} />,
}
