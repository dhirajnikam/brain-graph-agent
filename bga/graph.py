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

    def fetch_context(self, limit: int = 20) -> str:
        q = """
        MATCH (e:Entity)
        OPTIONAL MATCH (e)-[:MENTIONED_IN]->(s:Source)
        WITH e, collect(s.id)[0..3] AS sources, e.updatedAt AS updatedAt
        RETURN e.name AS name, e.type AS type, sources AS sources, updatedAt AS updatedAt
        ORDER BY updatedAt DESC
        LIMIT $limit
        """
        lines = []
        with self.driver() as drv:
            with drv.session() as s:
                for r in s.run(q, limit=limit):
                    srcs = ", ".join(r["sources"]) if r["sources"] else ""
                    lines.append(f"- {r['name']} ({r['type']})" + (f" [src: {srcs}]" if srcs else ""))
        return "\n".join(lines)

    # ---- Phase B/C BrainNode storage ----

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
        q = """
        MATCH (start:File {path: $path})
        CALL {
          WITH start
          MATCH p=(start)-[:IMPORTS*1..$hops]->(f:File)
          RETURN p
          LIMIT $limit
        }
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
