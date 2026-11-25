import AddChildDialog from './AddChildDialog.jsx'

// Add Child: a child node with the union form of its types, or a new NodeType.
export default {
  name: 'add_child',
  actions: ['add_child'],
  render: ({ node, ctx }) => <AddChildDialog node={node} meta={ctx.meta} onClose={ctx.close}
    onDone={(uid, msg) => { ctx.close(); ctx.notify(msg); if (uid) ctx.go(uid); else ctx.reload() }} />,
}
