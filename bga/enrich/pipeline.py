from __future__ import annotations

from typing import Any

from .extract import extract_facts
from .normalize import normalize_facts
from .types import EventType
from ..llm import LLM


def enrich(*, llm: LLM, event_type: EventType, payload: dict[str, Any], source: str):
    # E
    facts = extract_facts(llm=llm, event_type=event_type, payload=payload)
    # N
    nodes, edges = normalize_facts(facts=facts, source=source)
    return {"facts": facts, "nodes": nodes, "edges": edges}
