from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from .settings import Settings
from .llm import LLM
from .orchestrator import Orchestrator
from .graph import Graph
from .graph_memory import MemoryGraph
from .store_sqlite import SQLiteGraph


@dataclass
class AppState:
    settings: Settings
    llm: LLM
    graph: Any
    orch: Orchestrator


def make_state() -> AppState:
    st = Settings()
    llm = LLM(st)

    if st.graph_backend == "neo4j":
        g = Graph(st)
    elif st.graph_backend == "memory":
        g = MemoryGraph(st)
    else:
        g = SQLiteGraph(st)

    orch = Orchestrator(llm=llm, graph=g)
    g.ensure_schema()

    return AppState(settings=st, llm=llm, graph=g, orch=orch)


app = FastAPI(title="brain-graph-agent", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"] ,
    allow_headers=["*"],
)

STATE = make_state()


@app.get("/health")
def health():
    return {
        "ok": True,
        "graph_backend": STATE.settings.graph_backend,
        "mock_llm": STATE.settings.mock_llm,
        "model": STATE.settings.openai_model,
        "sqlite_path": os.getenv("BGA_SQLITE_PATH", os.path.abspath("./bga_graph.sqlite")),
    }


@app.post("/ingest")
def ingest(body: dict):
    """Ingest a message/event into the brain.

    body:
      {"text": "...", "source": "..."}
    """
    text = body.get("text", "")
    source = body.get("source", "api")
    out = STATE.orch.handle(text, source=source)
    return out


@app.get("/context")
def context(limit: int = 50):
    return {"context": STATE.graph.fetch_context(limit=limit)}


@app.get("/graph")
def graph(limit_nodes: int = 1000):
    if hasattr(STATE.graph, "export_graph"):
        return STATE.graph.export_graph(limit_nodes=limit_nodes)
    return JSONResponse(
        status_code=400,
        content={"error": "graph_backend_has_no_export", "backend": STATE.settings.graph_backend},
    )


@app.get("/ui", response_class=HTMLResponse)
def ui():
    # Minimal interactive UI using vis-network (CDN)
    html = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>brain-graph-agent UI</title>
  <script src="https://unpkg.com/vis-network@9.1.2/standalone/umd/vis-network.min.js"></script>
  <style>
    body { font-family: system-ui, sans-serif; margin: 0; }
    #top { display:flex; gap: 8px; padding: 10px; border-bottom: 1px solid #ddd; align-items:center; }
    #net { height: calc(100vh - 60px); }
    input, button { padding: 8px; }
    #log { white-space: pre-wrap; font-size: 12px; max-width: 480px; }
  </style>
</head>
<body>
  <div id="top">
    <button onclick="refresh()">Refresh graph</button>
    <input id="source" placeholder="source" value="ui" />
    <input id="text" placeholder="Type a sentence to ingest..." style="flex:1" />
    <button onclick="ingest()">Ingest</button>
    <span id="status" style="font-size:12px;color:#555"></span>
  </div>
  <div style="display:flex; height: calc(100vh - 60px);">
    <div id="net" style="flex: 1;"></div>
    <div id="log" style="width: 520px; border-left:1px solid #ddd; padding: 10px; overflow:auto;"></div>
  </div>

<script>
  let network;

  async function refresh() {
    const status = document.getElementById('status');
    status.textContent = 'Loading...';
    const g = await fetch('/graph').then(r => r.json());
    const nodes = new vis.DataSet(g.nodes.map(n => ({ id: n.id, label: n.label, group: n.type })));
    const edges = new vis.DataSet(g.edges.map(e => ({ id: e.id, from: e.from, to: e.to, label: e.label, arrows: 'to' })));

    const container = document.getElementById('net');
    const data = { nodes, edges };
    const options = {
      layout: { improvedLayout: true },
      physics: { stabilization: false },
      interaction: { hover: true },
    };

    network = new vis.Network(container, data, options);
    network.on('click', (params) => {
      if (!params.nodes.length) return;
      const id = params.nodes[0];
      const node = g.nodes.find(n => n.id === id);
      document.getElementById('log').textContent = JSON.stringify(node, null, 2);
    });

    status.textContent = `Nodes: ${g.nodes.length}  Edges: ${g.edges.length}`;
  }

  async function ingest() {
    const text = document.getElementById('text').value;
    if (!text.trim()) return;
    const source = document.getElementById('source').value || 'ui';
    const out = await fetch('/ingest', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ text, source })
    }).then(r => r.json());
    document.getElementById('log').textContent = JSON.stringify(out, null, 2);
    document.getElementById('text').value = '';
    await refresh();
  }

  refresh();
</script>
</body>
</html>
"""
    return HTMLResponse(html)
