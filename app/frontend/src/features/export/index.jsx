import ExportDialog from './ExportDialog.jsx'

// Export: download a branch as CSV or Excel, with a live preview.
export default {
  name: 'export',
  actions: ['export'],
  render: ({ node, ctx }) => <ExportDialog node={node} onClose={ctx.close} />,
}
