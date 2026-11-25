import { ConfirmDialog } from '../../components/Dialogs.jsx'
import DeleteDialog from './DeleteDialog.jsx'
import { deleteApi } from './api.js'
import { HELP } from '../../core/docs.js'

// Delete a node (children move up or go with it), or delete all children of a node.
export default {
  name: 'delete',
  actions: ['delete', 'delete_all_children'],
  render: ({ node, action, ctx }) => action?.ui === 'delete_all_children'
    ? <ConfirmDialog title="Delete all children" danger confirm="Delete" help={HELP.delete}
        text={<>Are you sure you want to delete the {node.child_count} {node.child_count === 1 ? 'child' : 'children'} of <span className="mono">{node.uid}</span> <strong>{node.name}</strong>?</>}
        onClose={ctx.close} onConfirm={async () => { const r = await deleteApi.deleteChildren(node.uid); ctx.done(`Deleted ${r.deleted} node(s) below ${node.name}`) }} />
    : <DeleteDialog node={node} onClose={ctx.close} onDone={(r, msg) => { ctx.close(); ctx.notify(msg); ctx.go(r.parent) }} />,
}
