import EditChildrenDialog from './EditChildrenDialog.jsx'

// Edit Children: every child of a node in an Excel-like grid, saved in one transaction.
export default {
  name: 'edit_children',
  actions: ['edit_children'],
  render: ({ node, ctx }) => <EditChildrenDialog node={node} onClose={() => { ctx.close(); ctx.reload() }} notify={ctx.notify} />,
}
