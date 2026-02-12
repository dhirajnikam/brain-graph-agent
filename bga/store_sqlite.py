from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Iterable

from .settings import Settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  props_json TEXT NOT NULL,
  updated_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS edges (
  id TEXT PRIMARY KEY,
  src TEXT NOT NULL,
  rel TEXT NOT NULL,
  dst TEXT NOT NULL,
  props_json TEXT NOT NULL,
  created_at_ms INTEGER NOT NULL,
  FOREIGN KEY(src) REFERENCES nodes(id),
  FOREIGN KEY(dst) REFERENCES nodes(id)
);

CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst);
CREATE INDEX IF NOT EXISTS idx_edges_rel ON edges(rel);
"""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _node_id(name: str) -> str:
    return name.strip().lower()


def _edge_id(src: str, rel: str, dst: str) -> str:
    return f"{src}::{rel}::{dst}"


@dataclass
class SQLiteGraph:
    """Persistent local graph (no Neo4j required)."""

    settings: Settings

    def _db_path(self) -> str:
        # default to workspace-local file for easy inspection
        p = os.getenv("BGA_SQLITE_PATH")
        if p:
            return os.path.expanduser(p)
        return os.path.abspath("./bga_graph.sqlite")

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._db_path())
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        return con

    def ensure_schema(self) -> None:
        with self._connect() as con:
            con.executescript(SCHEMA)

    def upsert_entities(self, entities: Iterable[dict[str, str]], *, source: str) -> None:
        now = _now_ms()
        src_node = f"source:{source}"
        with self._connect() as con:
            con.executescript(SCHEMA)
            con.execute(
                "INSERT OR REPLACE INTO nodes(id,type,props_json,updated_at_ms) VALUES(?,?,?,?)",
                (src_node, "Source", json.dumps({"id": source}), now),
            )
            for ent in entities:
                name = (ent.get("name") or "").strip()
                if not name:
                    continue
                typ = (ent.get("type") or "Entity").strip() or "Entity"
                nid = _node_id(name)
                props = {"name": name, "type": typ}
                con.execute(
                    "INSERT OR REPLACE INTO nodes(id,type,props_json,updated_at_ms) VALUES(?,?,?,?)",
                    (nid, "Entity", json.dumps(props), now),
                )
                eid = _edge_id(nid, "MENTIONED_IN", src_node)
                con.execute(
                    "INSERT OR REPLACE INTO edges(id,src,rel,dst,props_json,created_at_ms) VALUES(?,?,?,?,?,?)",
                    (eid, nid, "MENTIONED_IN", src_node, json.dumps({}), now),
                )

    def fetch_context(self, limit: int = 20) -> str:
        with self._connect() as con:
            cur = con.execute(
                "SELECT id, props_json FROM nodes WHERE type='Entity' ORDER BY updated_at_ms DESC LIMIT ?",
                (limit,),
            )
            lines: list[str] = []
            for nid, props_json in cur.fetchall():
                props = json.loads(props_json)
                name = props.get("name", nid)
                typ = props.get("type", "Entity")
                # include one source if exists
                src_cur = con.execute(
                    "SELECT dst FROM edges WHERE src=? AND rel='MENTIONED_IN' LIMIT 1",
                    (nid,),
                )
                src_row = src_cur.fetchone()
                src = src_row[0].replace("source:", "") if src_row else ""
                lines.append(f"- {name} ({typ})" + (f" [src: {src}]" if src else ""))
            return "\n".join(lines)

    def export_graph(self, limit_nodes: int = 2000) -> dict:
        with self._connect() as con:
            ncur = con.execute(
                "SELECT id,type,props_json,updated_at_ms FROM nodes ORDER BY updated_at_ms DESC LIMIT ?",
                (limit_nodes,),
            )
            nodes = [
                {
                    "id": r[0],
                    "label": json.loads(r[2]).get("name", r[0]),
                    "type": r[1],
                    "props": json.loads(r[2]),
                    "updatedAtMs": r[3],
                }
                for r in ncur.fetchall()
            ]
            node_ids = {n["id"] for n in nodes}
            ecur = con.execute(
                "SELECT id,src,rel,dst,props_json,created_at_ms FROM edges ORDER BY created_at_ms DESC LIMIT 5000"
            )
            edges = []
            for r in ecur.fetchall():
                if r[1] not in node_ids or r[3] not in node_ids:
                    continue
                edges.append(
                    {
                        "id": r[0],
                        "from": r[1],
                        "to": r[3],
                        "label": r[2],
                        "props": json.loads(r[4]),
                        "createdAtMs": r[5],
                    }
                )
            return {"nodes": nodes, "edges": edges}
