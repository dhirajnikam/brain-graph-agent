from __future__ import annotations

from dataclasses import dataclass

from .llm import LLM
from .graph import Graph

ORCH_SYSTEM = """You are an orchestrator. You MUST:
- use the provided CONTEXT (graph memory) and user message
- produce a friendly concise answer
- do not invent facts not in CONTEXT; if missing, ask one clarifying question.
"""

@dataclass
class Orchestrator:
    llm: LLM
    graph: Graph

    def handle(self, user_text: str, *, source: str) -> dict:
        # 1) extract entities and write to graph
        entities = self.llm.extract_entities(user_text)
        self.graph.upsert_entities(entities, source=source)

        # 2) fetch context pack
        context = self.graph.fetch_context(limit=30)

        # 3) worker response
        answer = self.llm.chat(system=ORCH_SYSTEM + "\nCONTEXT:\n" + context, user=user_text)

        # 4) judge
        judgement = self.llm.judge(goal="Respond to the user without hallucinating; be helpful.", answer=answer, context=context)

        return {
            "entities": entities,
            "context": context,
            "answer": answer,
            "judge": judgement,
        }
