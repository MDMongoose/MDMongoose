import GoldenRunDialog from './GoldenRunDialog.jsx'

// Golden records run: Refresh Golden Records, Run Matching and Run Survivorship open the same five-step run,
// each on its own step.
export default {
  name: 'golden_run',
  actions: ['refresh_golden', 'run_matching'],
  render: ({ node, action, ctx }) => <GoldenRunDialog node={node} ui={action?.ui} ctx={ctx} />,
}
