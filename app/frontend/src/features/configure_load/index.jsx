import { RunDialog } from '../../components/Dialogs.jsx'
import ConfigureLoadDialog from './ConfigureLoadDialog.jsx'

// Configure Load and Load Childs of a Table node.
export default {
  name: 'configure_load',
  actions: ['configure_load', 'load_children'],
  render: ({ node, action, ctx }) => action?.ui === 'load_children'
    ? <RunDialog node={node} action={action} onClose={() => { ctx.close(); ctx.reload() }} />
    : <ConfigureLoadDialog node={node} actions={ctx.actions} onClose={ctx.close} onSaved={ctx.done} />,
}
