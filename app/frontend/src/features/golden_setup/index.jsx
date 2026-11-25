import GoldenSetupDialog from './GoldenSetupDialog.jsx'

// Golden record setup: Edit Rule on a Master Data node, Edit Match Rule, Edit Rule on a Survivorship Rule
// and Train Match Model all open the same five-step dialog, each on its own step.
export default {
  name: 'golden_setup',
  actions: ['edit_rule', 'edit_match_rule', 'train_matching'],
  render: ({ node, action, ctx }) => <GoldenSetupDialog node={node} ui={action?.ui} ctx={ctx} />,
}
