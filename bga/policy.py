from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PolicyWarning:
    kind: str
    message: str
    evidence: list[str]


def simple_match(query: str, needles: list[str]) -> bool:
    q = (query or "").lower()
    return any((n or "").lower() in q for n in needles)


def fetch_negative_signals(*, graph, limit: int = 20) -> list[dict]:
    """Fetch recent NegativeSignal nodes (reverts, etc.)."""
    if not hasattr(graph, "driver"):
        return []

    q = """
    MATCH (n:BrainNode)
    WHERE n.label = 'NegativeSignal' AND coalesce(n.archived,false) = false
    RETURN n.id AS id, properties(n) AS props
    ORDER BY coalesce(n.updatedAt, 0) DESC
    LIMIT $limit
    """
    with graph.driver() as drv:
        with drv.session() as s:
            return [dict(r) for r in s.run(q, limit=limit)]


def warnings_for_plan(*, graph, plan: str) -> list[PolicyWarning]:
    neg = fetch_negative_signals(graph=graph, limit=50)
    warns: list[PolicyWarning] = []

    # MVP enforcement: surface "do not repeat" revert reasons when they match.
    for r in neg:
        props = r.get("props") or {}
        reason = (props.get("reason") or "").strip()
        kind = (props.get("kind") or "").strip()
        if not reason:
            continue
        if simple_match(plan, [reason]):
            warns.append(
                PolicyWarning(
                    kind=f"negative_learning:{kind or 'signal'}",
                    message=f"This plan matches a past negative-learning signal: {reason}",
                    evidence=[r.get("id")],
                )
            )

    return warns
