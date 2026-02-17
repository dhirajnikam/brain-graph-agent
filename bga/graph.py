from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from neo4j import GraphDatabase

from .settings import Settings


@dataclass
class Graph:
    settings: Settings

    def driver(self):
        return GraphDatabase.driver(
            self.settings.neo4j_uri,
            auth=(self.settings.neo4j_user, self.settings.neo4j_password),
        )

    def ensure_schema(self) -> None:
        # Constraints/indexes for both the legacy Entity demo and Phase C BrainNode storage.
        q = """
        CREATE CONSTRAINT entity_name IF NOT EXISTS
        FOR (e:Entity) REQUIRE e.name IS UNIQUE;

        CREATE INDEX entity_type IF NOT EXISTS
        FOR (e:Entity) ON (e.type);

        CREATE CONSTRAINT brain_node_id IF NOT EXISTS
        FOR (n:BrainNode) REQUIRE n.id IS UNIQUE;

        CREATE CONSTRAINT brain_source_id IF NOT EXISTS
        FOR (s:Source) REQUIRE s.id IS UNIQUE;

        CREATE INDEX brain_node_label IF NOT EXISTS
        FOR (n:BrainNode) ON (n.label);

        CREATE INDEX file_path IF NOT EXISTS
        FOR (f:File) ON (f.path);
        """
        with self.driver() as drv:
            with drv.session() as s:
                for stmt in [x.strip() for x in q.split(";") if x.strip()]:
                    s.run(stmt)

    # ---- Legacy demo API (still used by /ingest orchestrator) ----

    def upsert_entities(self, entities: Iterable[dict[str, str]], *, source: str) -> None:
        q = """
        UNWIND $entities AS ent
        MERGE (e:Entity {name: ent.name})
        SET e.type = ent.type,
            e.updatedAt = timestamp()
        WITH e
        MERGE (s:Source {id: $source})
        MERGE (e)-[:MENTIONED_IN]->(s)
        """
        with self.driver() as drv:
            with drv.session() as s:
                s.run(q, entities=list(entities), source=source)

    def fetch_context(self, limit: int = 30) -> str:
        """Return a compact, human-readable context snapshot.

        Prefers Phase C BrainNodes; falls back to legacy Entity nodes.
        """
        q_brain = """
        MATCH (n:BrainNode)
        WHERE coalesce(n.archived,false) = false AND n.label <> 'Source'
        OPTIONAL MATCH (n)-[:MENTIONED_IN]->(s:Source)
        WITH n, collect(s.id)[0..3] AS sources, n.updatedAt AS updatedAt
        RETURN n.label AS label,
               coalesce(n.name, n.path, n.what, n.hash, n.id) AS title,
               coalesce(n.why, n.reason, '') AS detail,
               sources AS sources,
               updatedAt AS updatedAt
        ORDER BY updatedAt DESC
        LIMIT $limit
        """

        q_legacy = """
        MATCH (e:Entity)
        OPTIONAL MATCH (e)-[:MENTIONED_IN]->(s:Source)
        WITH e, collect(s.id)[0..3] AS sources, e.updatedAt AS updatedAt
        RETURN e.name AS name, e.type AS type, sources AS sources, updatedAt AS updatedAt
        ORDER BY updatedAt DESC
        LIMIT $limit
        """

        lines: list[str] = []
        with self.driver() as drv:
            with drv.session() as s:
                brain = [dict(r) for r in s.run(q_brain, limit=limit)]
                if brain:
                    for r in brain:
                        srcs = ", ".join(r.get("sources") or [])
                        detail = (r.get("detail") or "").strip()
                        tail = (f" — {detail}" if detail else "")
                        lines.append(f"- [{r['label']}] {r['title']}{tail}" + (f" [src: {srcs}]" if srcs else ""))
                    return "\n".join(lines)

                for r in s.run(q_legacy, limit=limit):
                    srcs = ", ".join(r["sources"]) if r["sources"] else ""
                    lines.append(f"- {r['name']} ({r['type']})" + (f" [src: {srcs}]" if srcs else ""))

        return "\n".join(lines)

    # ---- Phase B/C BrainNode storage ----

    def resolve_conflicts(self, *, nodes: list[dict], edges: list[dict]) -> tuple[list[dict], list[dict]]:
        """If an incoming node conflicts with an existing node, version it and link EVOLVED_FROM.

        MVP rule:
        - If same id exists and key fields differ, create a new id with ::rev:<ms>
        - Add REL edge (EVOLVED_FROM) new -> old
        """
        import time

        ids = [n["id"] for n in nodes]
        if not ids:
            return nodes, edges

        existing: dict[str, dict] = {}
        q = """
        UNWIND $ids AS id
        MATCH (n:BrainNode {id: id})
        RETURN n.id AS id, properties(n) AS props
        """
        with self.driver() as drv:
            with drv.session() as s:
                for r in s.run(q, ids=ids):
                    existing[r["id"]] = r["props"]

        def key_fields(label: str) -> list[str]:
            return {
                "Decision": ["what", "why"],
                "Preference": ["name", "category"],
                "Pattern": ["name", "type"],
                "NegativeSignal": ["kind", "hash", "reason"],
                "Commit": ["hash", "message"],
                "File": ["path"],
            }.get(label, ["name", "path", "what"])

        id_map: dict[str, str] = {}
        new_nodes = []
        new_edges = list(edges)

        for n in nodes:
            oid = n["id"]
            ex = existing.get(oid)
            if not ex:
                new_nodes.append(n)
                continue

            label = n.get("label")
            keys = key_fields(label)
            conflict = False
            for k in keys:
                nv = (n.get("props") or {}).get(k)
                ev = ex.get(k)
                if nv is None:
                    continue
                if ev is None:
                    continue
                if str(nv).strip() != str(ev).strip():
                    conflict = True
                    break

            if not conflict:
                new_nodes.append(n)
                continue

            rev = int(time.time() * 1000)
            nid = f"{oid}::rev:{rev}"
            id_map[oid] = nid
            n2 = {**n, "id": nid, "props": {**(n.get("props") or {}), "base_id": oid}}
            new_nodes.append(n2)
            new_edges.append({
                "id": f"{nid}::EVOLVED_FROM::{oid}",
                "src": nid,
                "rel": "EVOLVED_FROM",
                "dst": oid,
                "props": {"reason": "conflict_detected"},
                "source": n.get("source", "api"),
            })

        if id_map:
            # rewrite edges to point at new node ids
            rew = []
            for e in new_edges:
                src = id_map.get(e.get("src"), e.get("src"))
                dst = id_map.get(e.get("dst"), e.get("dst"))
                e2 = {**e, "src": src, "dst": dst, "id": f"{src}::{e.get('rel')}::{dst}"}
                rew.append(e2)
            new_edges = rew

        return new_nodes, new_edges

    def upsert_brain_nodes_edges(self, *, nodes: list[dict], edges: list[dict]) -> None:
        """Upsert normalized nodes/edges into Neo4j with provenance."""
        q_nodes = """
        UNWIND $nodes AS n
        MERGE (bn:BrainNode {id: n.id})
        SET bn.label = n.label,
            bn.confidence = n.confidence,
            bn.source = n.source,
            bn.updatedAt = timestamp()
        SET bn += n.props
        """

        q_edges = """
        UNWIND $edges AS e
        MATCH (a:BrainNode {id: e.src})
        MATCH (b:BrainNode {id: e.dst})
        MERGE (a)-[r:REL {id: e.id}]->(b)
        SET r.type = e.rel,
            r.source = e.source,
            r.updatedAt = timestamp()
        SET r += e.props
        """

        q_edges_real = """
        UNWIND $edges AS e
        MATCH (a:BrainNode {id: e.src})
        MATCH (b:BrainNode {id: e.dst})
        FOREACH (_ IN CASE WHEN e.rel = 'IMPORTS' THEN [1] ELSE [] END |
          MERGE (a)-[:IMPORTS]->(b)
        )
        FOREACH (_ IN CASE WHEN e.rel = 'MENTIONED_IN' THEN [1] ELSE [] END |
          MERGE (a)-[:MENTIONED_IN]->(b)
        )
        FOREACH (_ IN CASE WHEN e.rel = 'ABOUT' THEN [1] ELSE [] END |
          MERGE (a)-[:ABOUT]->(b)
        )
        RETURN count(*)
        """

        with self.driver() as drv:
            with drv.session() as s:
                s.run(q_nodes, nodes=nodes)
                s.run(q_edges, edges=edges)
                s.run(q_edges_real, edges=edges)

                # Mirror File nodes into (:File {path}) for Phase A traversal compatibility.
                q_file_nodes = """
                UNWIND $nodes AS n
                WITH n WHERE n.label = 'File' AND n.props.path IS NOT NULL
                MERGE (f:File {path: n.props.path})
                SET f.updatedAt = timestamp()
                """
                s.run(q_file_nodes, nodes=nodes)

                q_file_imports = """
                UNWIND $edges AS e
                WITH e WHERE e.rel = 'IMPORTS'
                MATCH (a:BrainNode {id: e.src})
                MATCH (b:BrainNode {id: e.dst})
                WITH a,b
                WHERE a.path IS NOT NULL AND b.path IS NOT NULL
                MERGE (fa:File {path: a.path})
                MERGE (fb:File {path: b.path})
                MERGE (fa)-[:IMPORTS]->(fb)
                """
                s.run(q_file_imports, edges=edges)

    def export_brain(self, limit_nodes: int = 1000) -> dict:
        qn = """
        MATCH (n:BrainNode)
        RETURN n.id AS id,
               coalesce(n.name, n.path, n.what, n.hash, n.id) AS label,
               n.label AS type,
               properties(n) AS props
        ORDER BY n.updatedAt DESC
        LIMIT $limit
        """
        qe = """
        MATCH (a:BrainNode)-[r:REL]->(b:BrainNode)
        RETURN r.id AS id,
               a.id AS `from`,
               b.id AS `to`,
               r.type AS label,
               properties(r) AS props
        ORDER BY r.updatedAt DESC
        LIMIT 5000
        """
        with self.driver() as drv:
            with drv.session() as s:
                nodes = [dict(r) for r in s.run(qn, limit=limit_nodes)]
                edges = [dict(r) for r in s.run(qe)]
        return {"nodes": nodes, "edges": edges}

    def traverse_imports(self, *, start_path: str, hops: int = 2, limit: int = 30) -> dict:
        # NOTE: depends on File nodes/IMPORTS rels (Phase A). If absent, returns empty.
        # Neo4j does not allow parameters inside variable-length patterns; embed hops.
        hops_i = max(1, int(hops))
        q = f"""
        MATCH (start:File {{path: $path}})
        CALL {{
          WITH start
          MATCH p=(start)-[:IMPORTS*1..{hops_i}]->(f:File)
          RETURN p
          LIMIT $limit
        }}
        RETURN p
        """
        trace = {"start": start_path, "hops": hops, "paths": []}
        with self.driver() as drv:
            with drv.session() as s:
                try:
                    for r in s.run(q, path=start_path, hops=hops, limit=limit):
                        p = r["p"]
                        nodes = [n.get("path") for n in p.nodes]
                        trace["paths"].append(nodes)
                except Exception:
                    return trace
        return trace
