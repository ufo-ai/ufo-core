---
name: app-bridge
description: Load when an app homepage needs live workspace data or portal navigation. Ships bridge.js, the client an app page includes to read portal data, write objects, and open conversations over the postMessage channel to the portal shell.
---
# App bridge

`bridge.js` connects a homepage to the portal shell that frames it. Include it once, near the end of
the page: `<script src="./bridge.js"></script>` (deploy it as a sibling of the page's `index.html`).

It defines, on `window`:

- `ufoRead(path)` — returns a Promise resolving the JSON of the portal read at `path` (for example
  `"api/chats"` or `"objects/scheduled_task"`). Rejects if the read is refused.
- `ufoWrite(kind, name, spec)` — returns a Promise resolving the object write result
  `{ok, name, detail}`. `name` is required — a write names its object.
- `ufoNavigate(to)` — opens a portal route, e.g. `"#/c/<conversation-id>"` for a conversation or
  `"#/"` for a new conversation.
- `onInit(cb)` — `cb` receives `{member: {email, admin}, agentId}` once the shell is ready; do the
  page's first read there.

The read paths and their shapes belong to each app's own homepage skill, not here.
