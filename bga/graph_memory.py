from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable
import time

from .settings import Settings


@dataclass
class MemoryGraph:
    """Tiny in-memory graph backend.

    This exists so the repo works locally without Docker/Neo4j.
    Data is NOT persisted.
    """

    settings: Settings
    entities: dict[str, dict] = field(default_factory=dict)
    sources_by_entity: dict[str, set[str]] = field(default_factory=dict)

    def ensure_schema(self) -> None:
        return

    def upsert_entities(self, entities: Iterable[dict[str, str]], *, source: str) -> None:
        now = int(time.time() * 1000)
        for ent in entities:
            name = ent["name"].strip()
            if not name:
                continue
            self.entities[name.lower()] = {
                "name": name,
                "type": ent.get("type", "Entity"),
                "updatedAt": now,
            }
            self.sources_by_entity.setdefault(name.lower(), set()).add(source)

    def fetch_context(self, limit: int = 20) -> str:
        items = sorted(self.entities.values(), key=lambda x: x.get("updatedAt", 0), reverse=True)[:limit]
        lines = []
        for it in items:
            srcs = sorted(self.sources_by_entity.get(it["name"].lower(), set()))[:3]
            lines.append(f"- {it['name']} ({it['type']})" + (f" [src: {', '.join(srcs)}]" if srcs else ""))
        return "\n".join(lines)
