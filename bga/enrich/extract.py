from __future__ import annotations

from typing import Any

from .types import EventType, Fact
from ..llm import LLM


def extract_facts(*, llm: LLM, event_type: EventType, payload: dict[str, Any]) -> list[Fact]:
    facts: list[Fact] = []

    if event_type == "text":
        text = payload.get("text", "")
        ents = llm.extract_entities(text)
        for e in ents:
            facts.append(Fact(kind="text_entity", value=e, confidence=0.7))
        return facts

    if event_type == "decision":
        facts.append(
            Fact(
                kind="decision",
                value={
                    "what": payload.get("what", ""),
                    "why": payload.get("why", ""),
                    "when": payload.get("when"),
                },
                confidence=float(payload.get("confidence", 0.9)),
            )
        )
        return facts

    if event_type == "preference":
        facts.append(
            Fact(
                kind="preference",
                value={
                    "name": payload.get("name", ""),
                    "category": payload.get("category", "code_style"),
                },
                confidence=float(payload.get("confidence", 0.8)),
            )
        )
        return facts

    if event_type == "pattern":
        facts.append(
            Fact(
                kind="pattern",
                value={
                    "name": payload.get("name", ""),
                    "type": payload.get("type", "pattern"),
                },
                confidence=float(payload.get("confidence", 0.8)),
            )
        )
        return facts

    if event_type == "git_commit":
        facts.append(Fact(kind="git_commit", value={"hash": payload.get("hash"), "message": payload.get("message")}, confidence=1.0))
        return facts

    if event_type == "revert":
        facts.append(Fact(kind="revert", value={"hash": payload.get("hash"), "reason": payload.get("reason")}, confidence=1.0))
        return facts

    if event_type == "code_index":
        # expects {imports: [{from,to}, ...]}
        for it in payload.get("imports", []) or []:
            facts.append(Fact(kind="file_import", value={"from": it.get("from"), "to": it.get("to")}, confidence=1.0))
        return facts

    return facts
