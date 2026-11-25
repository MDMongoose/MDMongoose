# Customizing the app: adding an action with a dialog

## Where things live

```
src/mdm/                 the MDM library: node model, repository, rules, permissions, loading,
                         matching, survivorship, export. Shared by the app, the jobs and the pipelines.
src/mdm_custom/          your Python extensions: survivorship functions, background action handlers.

app/backend/             the Databricks App's API (FastAPI)
  main.py                the app object (uvicorn backend.main:app)
  service.py             MdmService = the service core + every feature's service class
  core/                  shared backend
    config.py            repository, current user, background job runner
    deps.py              svc(), runner(), me() for the routers
    service.py           ServiceCore: permission checks, node reads (tree, node view, search), editing
    routes.py            endpoints of the main screen (tree, nodes, edit, lock, move, runs, log)
    app.py               builds the app: error mapping, routers, the built UI
  features/<name>/       one folder per action dialog
    service.py           class <Name>Service with the feature's methods
    api.py               router = APIRouter() with the feature's endpoints

app/frontend/src/        the UI (React + Vite)
  core/                  the main screen: App, Hierarchy, Properties, api.js (fetch helper), cache.js
  components/            generic parts: Dialog, Busy, ConfirmDialog, RunDialog, Field, Tabs, Search, icons
  features/<name>/       one folder per action dialog
    index.jsx            which actions open the feature, and how its dialog is rendered
    <Name>Dialog.jsx     the dialog
    api.js               the feature's endpoints
  styles.css             all styles (pills = .badge / .chip, text boxes = .in, panels = .card / .box / .note)
```

Features are found automatically on both sides: a folder in `app/backend/features/` with a `service.py`
and/or `api.py`, and a folder in `app/frontend/src/features/` with an `index.jsx`. Nothing has to be
registered by hand.

`src/` vs `app/lib/`: `src/` is the source of the `mdm` packages. Older versions of the build copied it
into `app/lib/`; nothing reads that folder any more (it is in `.gitignore`), so it can be deleted.

## How an action reaches its dialog

1. An **Action node** (Model › Actions) has a `ui` key, a `mode`, an `action_group` (the toolbar box) and a
   **Required Level**. A NodeType lists the actions its nodes get.
2. The toolbar shows the actions of the selected node. A click opens the frontend feature whose
   `index.jsx` lists that `ui` key.
3. The dialog calls its `api.js`, which calls the backend feature's `api.py`, which calls a method of its
   `service.py`.

Edit, Lock and Change Parent work on the main screen and need no feature. A background action without a
dialog (`mode: job`, handler in `mdm_custom`) needs no feature either: the generic run dialog starts it
and follows the run.

## Adding an action with a dialog — the files

Example: an action **Tag Children** (`ui` key `tag_children`) that sets a property on every child of a
node. Use the same feature name on both sides.

### 1. The Action node — `src/mdm/model.py`, `src/mdm/seed.py`

```python
# model.py, class K: a free seed number between 1211 and 1299
A_TAG_CHILDREN = to_uid(1211)

# seed.py: the default Required Level of its ui key
ACTION_LEVELS = {..., "tag_children": MODIFIER}

# seed.py, next to the other Model actions: number, name, folder, handler, params, group (toolbar box), ui, mode
_action(1211, "Tag Children", actions, "mdm.actions.tag_children", "UID, Tag", "Children", "tag_children", "sync"),

# seed.py: add K.A_TAG_CHILDREN to the actions of the NodeTypes that should have it
```

Run `mdm_init` after deploying: it adds the new Action node and the NodeType's new action to an existing
deployment without touching user edits.

### 2. Backend — `app/backend/features/tag_children/`

`service.py`:

```python
"""Tag Children: set a property on every child of a node."""

from __future__ import annotations

from mdm.hierarchy import chain_of
from mdm.model import K, clone


class TagChildrenService:
    def tag_children(self, user, uid: str, tag: str) -> dict:
        node = self._node(uid)
        self._need_action(user, chain_of(self.repo, node), K.A_TAG_CHILDREN)
        kids = [clone(c) for c in self.repo.children(uid) if not c["locked"]]
        for c in kids:
            c["properties"]["Tag"] = tag
        self.repo.upsert(kids, user.name)
        return {"tagged": len(kids)}
```

The class name must end with `Service`. Its methods run on `MdmService`, so they can use the core helpers:
`self._node(uid)`, `self._need_action(user, chain, action)`, `self._need(user, chain, level)`,
`self._not_locked(node)`, `self.repo` (reads and writes), `self.catalog`. Raise `RuleError` for a rule the
user broke (HTTP 400) and `Forbidden` for a missing permission (403).

`api.py`:

```python
"""Tag Children endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request

from ...core.deps import me, svc

router = APIRouter()


@router.post("/api/nodes/{uid}/tag-children")
def api_tag_children(uid: str, request: Request, body: dict[str, Any] = Body(...)):
    return svc().tag_children(me(request), uid, body.get("tag") or "")
```

Add an empty `__init__.py` to the folder.

### 3. Frontend — `app/frontend/src/features/tag_children/`

`api.js`:

```js
// Tag Children endpoints.
import { call } from '../../core/api.js'

export const tagChildrenApi = {
  tag: (uid, tag) => call('POST', `/api/nodes/${uid}/tag-children`, { tag }),
}
```

`TagChildrenDialog.jsx`:

```jsx
import { useState } from 'react'
import { Dialog, Field, useBusy } from '../../components/Dialogs.jsx'
import { tagChildrenApi } from './api.js'

// Tag Children: set a tag on every child of the node.
export default function TagChildrenDialog({ node, onClose, onDone }) {
  const [tag, setTag] = useState('')
  const [busy, err, run] = useBusy()
  const apply = () => run(async () => { const r = await tagChildrenApi.tag(node.uid, tag); onDone(`${r.tagged} children tagged`) })
  return (
    <Dialog title="Tag Children" sub={node.name} onClose={onClose} busy={busy}
      footer={<><span />{err && <span className="error">{err}</span>}
        <div className="r"><button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn pri" disabled={!tag || busy} onClick={apply}>Tag</button></div></>}>
      <div className="dbody">
        <Field label="Tag" htmlFor="tag"><input id="tag" className="in" value={tag} onChange={(e) => setTag(e.target.value)} data-autofocus /></Field>
      </div>
    </Dialog>
  )
}
```

`index.jsx`:

```jsx
import TagChildrenDialog from './TagChildrenDialog.jsx'

// Tag Children: set a tag on every child of a node.
export default {
  name: 'tag_children',
  actions: ['tag_children'],
  render: ({ node, action, ctx }) => <TagChildrenDialog node={node} onClose={ctx.close} onDone={ctx.done} />,
}
```

`ctx` gives the dialog what it needs from the main screen:

| | |
|---|---|
| `ctx.close()` | close the dialog |
| `ctx.done(message)` | close, show the message, reload the screen |
| `ctx.notify(message, isError)` | show a message |
| `ctx.reload()` | reload the screen (after a write) |
| `ctx.go(uid)` | open another node |
| `ctx.open(uiKey, { node })` | open another feature's dialog |
| `ctx.meta`, `ctx.actions` | NodeTypes and groups; the actions of the selected node |

### 4. A test — `tests/test_tag_children.py`

```python
from backend.service import MdmService
from mdm.repo import MemoryRepo
from mdm.seed import all_seed
from test_core import ALICE


def test_tag_children():
    s = MdmService(MemoryRepo(all_seed("workspace", "mdm_staging", demo=True)))
    ...
```

## Checklist

| Step | File |
|---|---|
| Action uid | `src/mdm/model.py` (K) |
| Action node, default level, NodeType's action list | `src/mdm/seed.py` |
| Service method | `app/backend/features/<name>/service.py` |
| Endpoint | `app/backend/features/<name>/api.py` (+ empty `__init__.py`) |
| Endpoint call | `app/frontend/src/features/<name>/api.js` |
| Dialog | `app/frontend/src/features/<name>/<Name>Dialog.jsx` |
| Action → dialog | `app/frontend/src/features/<name>/index.jsx` |
| Test | `tests/test_<name>.py` |
| Docs | the website's docs and screenshots |
