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
    """Ingest raw text into the legacy orchestrator (kept for backwards compatibility)."""
    text = body.get("text", "")
    source = body.get("source", "api")
    out = STATE.orch.handle(text, source=source)
    return out


@app.post("/event")
def event(body: dict):
    """Phase C: Structured ENRICH ingestion.

    body:
      {
        "type": "text|decision|preference|pattern|git_commit|revert|code_index",
        "source": "...",
        "payload": { ... }
      }
    """
    from .enrich.pipeline import enrich

    etype = body.get("type", "text")
    source = body.get("source", "api")
    payload = body.get("payload", {})

    out = enrich(llm=STATE.llm, event_type=etype, payload=payload, source=source)

    # write to graph if supported
    if hasattr(STATE.graph, "upsert_brain_nodes_edges"):
        nodes = [
            {
                "label": n.label,
                "id": n.id,
                "props": {**n.props, "confidence": n.confidence, "source": n.source},
                "confidence": n.confidence,
                "source": n.source,
            }
            for n in out["nodes"]
        ]
        edges = [
            {
                "id": f"{e.src}::{e.rel}::{e.dst}",
                "src": e.src,
                "rel": e.rel,
                "dst": e.dst,
                "props": e.props,
                "source": e.source,
            }
            for e in out["edges"]
        ]
        # C (Connect): link nodes co-occurring in the same event to preserve locality.
        node_ids = [n["id"] for n in nodes if n.get("label") != "Source"]
        for i in range(min(len(node_ids), 20)):
            for j in range(i + 1, min(len(node_ids), 20)):
                a, b = node_ids[i], node_ids[j]
                edges.append({
                    "id": f"{a}::RELATED_TO::{b}",
                    "src": a,
                    "rel": "RELATED_TO",
                    "dst": b,
                    "props": {"reason": "co_occurrence"},
                    "source": source,
                })

        if hasattr(STATE.graph, "resolve_conflicts"):
            nodes, edges = STATE.graph.resolve_conflicts(nodes=nodes, edges=edges)
        STATE.graph.upsert_brain_nodes_edges(nodes=nodes, edges=edges)

    return {
        "ok": True,
        "type": etype,
        "source": source,
        "facts": [f.__dict__ for f in out["facts"]],
        "nodes": [n.__dict__ for n in out["nodes"]],
        "edges": [e.__dict__ for e in out["edges"]],
    }


@app.post("/housekeep")
def housekeep(body: dict | None = None):
    """Phase C: housekeeping + optional consolidation.

    body (optional):
      {"consolidate": true}

    Actions:
    - compute decay + importance
    - archive low-value nodes
    - optional: create Summary nodes for archived clusters (no hard deletes)
    """
    body = body or {}
    consolidate = bool(body.get("consolidate", False))

    if not hasattr(STATE.graph, "driver"):
        return {"ok": False, "error": "backend_not_supported"}

    q_score = """
    MATCH (n:BrainNode)
    OPTIONAL MATCH (n)--(m)
    WITH n,
         count(m) AS degree,
         (timestamp() - coalesce(n.updatedAt, timestamp())) / 86400000.0 AS ageDays

    WITH n, degree, ageDays,
         CASE
           WHEN ageDays > 90 THEN 0.30
           WHEN ageDays > 30 THEN 0.50
           WHEN ageDays > 7 THEN 0.80
           ELSE 0.95
         END AS decay,
         coalesce(n.confidence, 0.5) AS confidence,
         coalesce(n.access_count, 0) AS access_count,
         coalesce(n.user_signal, 0.0) AS user_signal

    SET n.decay = decay

    SET n.importance = (
      0.25 * decay +
      0.20 * (CASE WHEN access_count > 0 THEN 1.0 ELSE 0.2 END) +
      0.20 * (CASE WHEN degree > 5 THEN 1.0 WHEN degree > 0 THEN 0.6 ELSE 0.2 END) +
      0.15 * confidence +
      0.20 * (CASE WHEN user_signal > 0 THEN 1.0 ELSE 0.2 END)
    )

    SET n.archived = CASE
      WHEN n.label = 'Source' THEN false
      WHEN confidence < 0.2 THEN true
      WHEN ageDays > 180 THEN true
      WHEN n.importance < 0.15 THEN true
      ELSE false
    END

    RETURN count(n) AS updated
    """

    q_consolidate = """
    // Create one Summary per (label, month) for archived nodes.
    MATCH (n:BrainNode)
    WHERE coalesce(n.archived,false) = true AND n.label <> 'Source'
    WITH n,
         n.label AS label,
         toString(date(datetime({epochMillis: coalesce(n.updatedAt, timestamp())}))) AS d
    WITH n, label, substring(d, 0, 7) AS ym
    WITH label, ym, collect(n)[0..200] AS nodes

    WITH label, ym, nodes,
         [x IN nodes | coalesce(x.name, x.path, x.what, x.hash, x.id)][0..10] AS samples,
         size(nodes) AS cnt

    MERGE (s:BrainNode {id: 'summary:' + label + ':' + ym})
    SET s.label = 'Summary',
        s.type = label,
        s.ym = ym,
        s.count = cnt,
        s.samples = samples,
        s.updatedAt = timestamp(),
        s.archived = false,
        s.importance = 0.25

    WITH s, nodes
    UNWIND nodes AS n
    MERGE (s)-[:SUMMARIZES]->(n)

    RETURN count(s) AS summaries
    """

    with STATE.graph.driver() as drv:
        with drv.session() as s:
            updated = s.run(q_score).single()["updated"]
            summaries = 0
            if consolidate:
                summaries = s.run(q_consolidate).single()["summaries"]

    return {"ok": True, "updated": updated, "consolidated": consolidate, "summaries": summaries}


@app.get("/context")
def context(limit: int = 50):
    return {"context": STATE.graph.fetch_context(limit=limit)}


@app.post("/policy")
def policy(body: dict):
    """Phase C upgrade: policy check for a proposed plan.

    body: {"plan": "..."}
    """
    from .policy import warnings_for_plan

    plan = body.get("plan", "")
    warns = warnings_for_plan(graph=STATE.graph, plan=plan)
    return {"ok": True, "warnings": [w.__dict__ for w in warns]}


@app.post("/retrieve")
def retrieve(body: dict):
    """Phase B: retrieval with full trace.

    body:
      {
        "query": "...",
        "current_file": "src/auth/jwt.ts" (optional),
        "mode": "fast|balanced|thorough" (optional)
      }
    """
    query = body.get("query", "")
    current_file = body.get("current_file")
    mode = body.get("mode", "balanced")
    priority = body.get("priority", "quality")  # quality|cheap

    intent = STATE.llm.intent(query=query, current_file=current_file)
    hops = int(intent.get("hops", 2))
    token_budget = int(intent.get("token_budget", 1500))

    trace = {"intent": intent, "traversal": None, "selection": []}

    # Optional traversal if graph backend supports it.
    if current_file and hasattr(STATE.graph, "traverse_imports"):
        trace["traversal"] = STATE.graph.traverse_imports(start_path=current_file, hops=hops, limit=50)

        # flatten unique file paths from traversal
        files = []
        seen = set()
        for path_nodes in trace["traversal"].get("paths", []):
            for p in path_nodes[1:]:
                if not p or p in seen:
                    continue
                seen.add(p)
                files.append(p)

        # Score by first-seen order (MVP). Later: centrality/recency/importance.
        for i, f in enumerate(files[:20]):
            score = 1.0 / (i + 1)
            trace["selection"].append({"type": "file", "id": f, "score": score, "reason": "import-graph"})

    # Always include a small memory context snapshot.
    ctx = STATE.graph.fetch_context(limit=30)

    # Add negative-learning signals (Phase C upgrade)
    neg_lines = []
    if hasattr(STATE.graph, "driver"):
        qneg = """
        MATCH (n:BrainNode)
        WHERE n.label='NegativeSignal' AND coalesce(n.archived,false)=false
        RETURN coalesce(n.reason,'') AS reason, coalesce(n.hash,'') AS hash
        ORDER BY coalesce(n.updatedAt,0) DESC
        LIMIT 10
        """
        with STATE.graph.driver() as drv:
            with drv.session() as s:
                for r in s.run(qneg):
                    reason = (r["reason"] or "").strip()
                    h = (r["hash"] or "").strip()
                    if reason or h:
                        neg_lines.append(f"- {reason}" + (f" (commit {h})" if h else ""))

    # Build context pack within token budget (approx by chars for MVP)
    context_pack = """CONTEXT (brain snapshot):\n""" + (ctx or "(empty)")
    if neg_lines:
        context_pack += "\n\nNEGATIVE LEARNINGS (avoid repeating):\n" + "\n".join(neg_lines)
    if trace["selection"]:
        context_pack += "\n\nRELATED FILES (from graph traversal):\n" + "\n".join([f"- {x['id']} (score={x['score']:.2f})" for x in trace["selection"]])

    # Model routing (simple mapping)
    model = "gpt-5.1"
    if mode == "fast" and priority == "cheap":
        model = "gpt-5-mini"
    elif mode == "fast" and priority == "quality":
        model = "gpt-5.1"
    elif mode == "thorough" and priority == "quality":
        model = "gpt-5.2-codex"

    return {
        "mode": mode,
        "priority": priority,
        "model": model,
        "token_budget": token_budget,
        "trace": trace,
        "context_pack": context_pack,
    }


@app.get("/graph")
def graph(limit_nodes: int = 1000):
    # Prefer Phase C brain export when available
    if hasattr(STATE.graph, "export_brain"):
        return STATE.graph.export_brain(limit_nodes=limit_nodes)
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
