// Where the MDMongoose documentation is published; the ? button of a dialog opens its section there.
export const DOCS_BASE = 'https://mdmongoose.com/docs'

// Dialog → documentation page and section (the anchors are the slugs of the headings).
export const HELP = {
  add_child: 'modifying-data#add-a-child',
  node_type: 'enum-link-array#defining-these-properties-on-a-nodetype',
  change_parent: 'modifying-data#move-link-and-delete',
  delete: 'modifying-data#move-link-and-delete',
  child_link: 'modifying-data#move-link-and-delete',
  browse: 'modifying-data#browse-restructure-many-subtrees-at-once',
  edit_children: 'edit-children',
  export: 'export-csv-excel',
  configure_load: 'importing-data#3-create-the-table-node-and-configure-the-load',
  load_children: 'importing-data#4-refresh-the-data',
  golden_setup: 'matching#golden-record-setup',
  golden_setup_steps: ['matching#1--structure--sources', 'matching#2--standardize--clean', 'matching#3--filter--blocking',
    'matching#4--matching-rules', 'survivorship-rules#the-rule-editor'],
  golden_run: 'master-data-records#refresh-the-golden-records--the-golden-records-run',
  review_matches: 'matching#review-matches',
  record_matching: 'matching#fix-one-record-or-golden-record',
  action: 'user-guide#actions',
}

export const docUrl = (page) => `${DOCS_BASE}/${page}`
