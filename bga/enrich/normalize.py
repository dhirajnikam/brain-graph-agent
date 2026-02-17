from __future__ import annotations

import re
from typing import Any

from .types import Fact, NormalizedNode, NormalizedEdge


def canon(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s


def canon_id(prefix: str, name: str) -> str:
    return f"{prefix}:{canon(name).lower()}"


def normalize_facts(*, facts: list[Fact], source: str) -> tuple[list[NormalizedNode], list[NormalizedEdge]]:
    nodes: list[NormalizedNode] = []
    edges: list[NormalizedEdge] = []

    for f in facts:
        k = f.kind
        v: dict[str, Any] = f.value
        conf = float(f.confidence)

        if k == "text_entity":
            name = canon(v.get("name", ""))
            typ = canon(v.get("type", "Entity")) or "Entity"
            nid = canon_id("entity", name)
            nodes.append(
                NormalizedNode(
                    label="Entity",
                    id=nid,
                    props={"name": name, "type": typ},
                    confidence=conf,
                    source=source,
                )
            )
            # Link to Source
            sid = f"source:{source}"
            nodes.append(
                NormalizedNode(
                    label="Source",
                    id=sid,
                    props={"id": source},
                    confidence=1.0,
                    source=source,
                )
            )
            edges.append(NormalizedEdge(src=nid, rel="MENTIONED_IN", dst=sid, props={}, source=source))

        elif k == "decision":
            what = canon(v.get("what", ""))
            why = canon(v.get("why", ""))
            did = canon_id("decision", what)
            nodes.append(
                NormalizedNode(
                    label="Decision",
                    id=did,
                    props={"what": what, "why": why, "when": v.get("when")},
                    confidence=conf,
                    source=source,
                )
            )

        elif k == "preference":
            name = canon(v.get("name", ""))
            category = canon(v.get("category", "code_style"))
            pid = canon_id("pref", f"{category}:{name}")
            nodes.append(
                NormalizedNode(
                    label="Preference",
                    id=pid,
                    props={"name": name, "category": category},
                    confidence=conf,
                    source=source,
                )
            )

        elif k == "pattern":
            name = canon(v.get("name", ""))
            ptype = canon(v.get("type", "pattern"))
            patid = canon_id("pattern", f"{ptype}:{name}")
            nodes.append(
                NormalizedNode(
                    label="Pattern",
                    id=patid,
                    props={"name": name, "type": ptype},
                    confidence=conf,
                    source=source,
                )
            )

        elif k == "file_import":
            # value: {from: "a", to: "b"}
            a = canon(v.get("from", ""))
            b = canon(v.get("to", ""))
            if a and b:
                na = canon_id("file", a)
                nb = canon_id("file", b)
                nodes.append(NormalizedNode("File", na, {"path": a}, 1.0, source))
                nodes.append(NormalizedNode("File", nb, {"path": b}, 1.0, source))
                edges.append(NormalizedEdge(na, "IMPORTS", nb, {}, source))

        elif k == "git_commit":
            h = canon(v.get("hash", ""))
            if h:
                cid = f"commit:{h.lower()}"
                nodes.append(NormalizedNode("Commit", cid, {"hash": h, "message": v.get("message")}, 1.0, source))

        elif k == "revert":
            h = canon(v.get("hash", ""))
            if h:
                cid = f"commit:{h.lower()}"
                nodes.append(NormalizedNode("Commit", cid, {"hash": h}, 1.0, source))
                nid = canon_id("negative", f"revert:{h}")
                nodes.append(NormalizedNode("NegativeSignal", nid, {"kind": "revert", "hash": h, "reason": v.get("reason")}, 1.0, source))
                edges.append(NormalizedEdge(nid, "ABOUT", cid, {}, source))

    # Always create a Source node and link created nodes to it (provenance).
    sid = f"source:{source}"
    nodes.append(
        NormalizedNode(
            label="Source",
            id=sid,
            props={"id": source},
            confidence=1.0,
            source=source,
        )
    )
    for n in list(nodes):
        if n.id == sid:
            continue
        # connect everything to Source for traceability
        edges.append(NormalizedEdge(src=n.id, rel="MENTIONED_IN", dst=sid, props={}, source=source))

    # De-dupe nodes by (label,id) keeping latest props merged
    merged: dict[tuple[str, str], NormalizedNode] = {}
    for n in nodes:
        key = (n.label, n.id)
        if key not in merged:
            merged[key] = n
        else:
            merged[key].props.update({k: v for k, v in n.props.items() if v is not None})
            merged[key].confidence = max(merged[key].confidence, n.confidence)

    # De-dupe edges
    e_seen = set()
    uniq_edges: list[NormalizedEdge] = []
    for e in edges:
        k = (e.src, e.rel, e.dst)
        if k in e_seen:
            continue
        e_seen.add(k)
        uniq_edges.append(e)

    return list(merged.values()), uniq_edges
