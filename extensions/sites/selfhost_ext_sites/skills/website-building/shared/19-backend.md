# Long-Running Backend Servers

Run a real server process (FastAPI, Express, Flask, etc.) inside the sandbox. Use this when you need:

- WebSocket or SSE streaming
- In-memory state across requests
- Framework features (middleware, dependency injection, ORMs)
- Background tasks or scheduled work
- Multiple related endpoints with shared state
- Runtime LLM features (a chat box, on-the-fly summaries) — **read `shared/20-llm-api.md`** for the Anthropic-over-egress API and usage examples

## How It Works

The cleanest setup serves the frontend **and** the API from one server on one port, so everything shares a single origin (`http://localhost:<port>`) and the frontend can call the API with relative `/api/...` paths — no cross-origin, no URL injection.

1. Write a server that listens on a port (e.g., 8000) and serves both your static files and `/api/...` routes
2. During the build, run it with `start_server(command=…, project_path=…, port=…)` to test
3. Ship it with `publish_website(project_path=…, app_name=…, install_command=…, run_command=…)` — it installs dependencies, runs your server, and returns the reachable URL

The server is reachable at `http://localhost:<port>` inside the sandbox. Bind to `0.0.0.0` so the readiness probe on `127.0.0.1` connects.

## Step-by-Step

### 1. Write the Server

Create a standard server in your project directory. Mount your static frontend on the same app so it shares the API's origin. Example with FastAPI:

```python
#!/usr/bin/env python3
"""api_server.py — runs on port 8000 inside the sandbox."""
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

db = sqlite3.connect("data.db", check_same_thread=False)
db.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")

@asynccontextmanager
async def lifespan(app):
    yield
    db.close()

app = FastAPI(lifespan=lifespan)

class Item(BaseModel):
    name: str

@app.get("/api/items")
def list_items():
    rows = db.execute("SELECT id, name, created_at FROM items ORDER BY id").fetchall()
    return [{"id": r[0], "name": r[1], "created_at": r[2]} for r in rows]

@app.post("/api/items", status_code=201)
def create_item(item: Item):
    cur = db.execute("INSERT INTO items (name) VALUES (?)", [item.name])
    db.commit()
    return {"id": cur.lastrowid, "name": item.name}

@app.delete("/api/items/{item_id}")
def delete_item(item_id: int):
    db.execute("DELETE FROM items WHERE id = ?", [item_id])
    db.commit()
    return {"deleted": item_id}

# Serve the static frontend from the same origin (mount last, after the API routes).
app.mount("/", StaticFiles(directory="public", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

### 2. Start the Server (during the build)

```
start_server(command="python api_server.py", project_path="/workspace/my-project", port=8000)
```

`start_server` kills any existing process on the port, starts the command in the background, and polls until the port is listening. It returns the URL and the log path — no manual health check needed. Install the server's dependencies first (`pip install …` / `npm install …` via `bash`), since web frameworks are not baked into the image.

The sandbox already carries `ANTHROPIC_API_KEY` in its environment for runtime LLM calls (see `shared/20-llm-api.md`); egress works while the turn is running.

### 3. Call the API from the Frontend

Same origin → use relative paths, no placeholders:

```js
async function loadItems() {
  const res = await fetch('/api/items');
  return res.json();
}

async function addItem(name) {
  const res = await fetch('/api/items', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  return res.json();
}
```

### 4. Ship It

```
publish_website(
  project_path="/workspace/my-project",
  dist_path="public",
  app_name="My App",
  install_command="pip install -r requirements.txt",
  run_command="python api_server.py"
)
```

Returns the reachable `http://localhost:<port>` URL inside the sandbox. Re-run to update.

## WebSocket Example

Server (Python with websockets):

```python
#!/usr/bin/env python3
import asyncio
import websockets

async def echo(websocket):
    async for message in websocket:
        await websocket.send(f"echo: {message}")

async def main():
    async with websockets.serve(echo, "0.0.0.0", 8000):
        await asyncio.Future()

asyncio.run(main())
```

Client — build the URL from the page's own origin so it points at the same host:

```js
const ws = new WebSocket(`${location.origin.replace(/^http/, 'ws')}/ws`);
```

## Express.js Example

```js
// server.js
const express = require('express');
const app = express();
app.use(express.json());
app.use(express.static('public')); // serve the static frontend from the same origin

let items = [];
let nextId = 1;

app.get('/api/items', (req, res) => res.json(items));
app.post('/api/items', (req, res) => {
  const item = { id: nextId++, ...req.body };
  items.push(item);
  res.status(201).json(item);
});

app.listen(8000, '0.0.0.0', () => console.log('listening on 8000'));
```

```
start_server(command="node server.js", project_path="/workspace/my-project", port=8000)
```

## Notes

- **Bind to `0.0.0.0`** — so the readiness probe on `127.0.0.1` connects
- **One origin is simplest** — serving static files and the API from the same server avoids cross-origin entirely. If you must split them onto different ports, each is a separate `http://localhost:<port>` origin: enable CORS (`allow_origins=["*"]`) and point the frontend at the backend's full URL
- **Visitor state** — cookies, `localStorage`, and `sessionStorage` all work on the served origin; use them for per-visitor data
- SSE (`text/event-stream`), chunked responses, long-polling, and WebSockets all work
