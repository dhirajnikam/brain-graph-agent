from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

EventType = Literal[
    "text",
    "decision",
    "preference",
    "pattern",
    "git_commit",
    "revert",
    "code_index",
]


@dataclass
class Fact:
    kind: str
    value: dict[str, Any]
    confidence: float


@dataclass
class NormalizedNode:
    label: str          # e.g. "Decision", "Preference", "File", "User"
    id: str             # canonical id
    props: dict[str, Any]
    confidence: float
    source: str


@dataclass
class NormalizedEdge:
    src: str
    rel: str
    dst: str
    props: dict[str, Any]
    source: str
