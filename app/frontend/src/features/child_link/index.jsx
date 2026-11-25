import ChildLinkDialog from './ChildLinkDialog.jsx'

// Add Child Link: shows an existing node in a second place without copying it.
export default {
  name: 'child_link',
  actions: ['add_child_link'],
  render: ({ node, ctx }) => <ChildLinkDialog node={node} onClose={ctx.close} onDone={ctx.done} />,
}
