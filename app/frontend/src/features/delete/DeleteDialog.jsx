import { useEffect, useState } from 'react'
import { Dialog, useBusy } from '../../components/Dialogs.jsx'
import { HELP } from '../../core/docs.js'
import { deleteApi } from './api.js'

// Delete a node; when it has children, they either move up to its parent or are deleted with it.
export default function DeleteDialog({ node, onClose, onDone }) {
  const [p, setP] = useState(null)
  const [mode, setMode] = useState('move')
  const [busy, err, run] = useBusy()
  useEffect(() => { deleteApi.deletePreview(node.uid).then(setP).catch(() => setP({ descendants: 0, locked: 0, loaded: 0 })) }, [node.uid])
  const parent = node.path.at(-2)?.name || 'its parent'
  const hasKids = p && p.descendants > 0
  const blocked = hasKids && mode === 'tree' && p.locked > 0
  return (
    <Dialog title="Delete node" onClose={onClose} size="sm" role="alertdialog" labelledBy="dlt" busy={!p || busy} busyLabel={p ? 'Deleting…' : undefined}
      help={HELP.delete}
      footer={<div className="r"><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn dng" disabled={!p || blocked || busy} data-autofocus onClick={() => run(async () => {
          const r = await deleteApi.remove(node.uid, hasKids ? mode : 'tree')
          onDone(r, r.moved ? `Deleted ${node.name}; ${r.moved} child node(s) moved to ${parent}` : `Deleted ${node.name}${r.deleted > 1 ? ` and ${r.deleted - 1} node(s) below it` : ''}`)
        })}>Delete</button></div>}>
      <div className="dbody" style={{ gap: 14 }}>
        <div style={{ fontSize: 14 }}>Are you sure you want to delete <span className="mono">{node.uid}</span> <strong>{node.name}</strong>?</div>
        {hasKids && (
          <div role="radiogroup" aria-label="What happens to the children" className="stack">
            <button role="radio" aria-checked={mode === 'move'} className={`card-radio ${mode === 'move' ? 'on' : ''}`} onClick={() => setMode('move')}>
              <strong>Move children to parent</strong><span className="sub">Its {node.child_count} child node(s) move to “{parent}”.</span></button>
            <button role="radio" aria-checked={mode === 'tree'} className={`card-radio ${mode === 'tree' ? 'on' : ''}`} onClick={() => setMode('tree')}>
              <strong>Delete the whole subtree</strong><span className="sub">{p.descendants} node(s) below are deleted too.</span></button>
          </div>
        )}
        {blocked && <div className="error">{p.locked} locked node(s) below — unlock them first, or move the children.</div>}
        {err && <div className="error">{err}</div>}
      </div>
    </Dialog>
  )
}
