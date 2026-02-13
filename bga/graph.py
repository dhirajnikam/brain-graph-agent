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
        q = """
        CREATE CONSTRAINT entity_name IF NOT EXISTS
        FOR (e:Entity) REQUIRE e.name IS UNIQUE;

        CREATE INDEX entity_type IF NOT EXISTS
        FOR (e:Entity) ON (e.type);
        """
        with self.driver() as drv:
            with drv.session() as s:
                for stmt in [x.strip() for x in q.split(";") if x.strip()]:
                    s.run(stmt)

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
        # Neo4j: when returning aggregates, ORDER BY cannot reference pre-aggregation vars.
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
