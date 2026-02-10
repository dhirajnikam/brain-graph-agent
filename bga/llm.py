"""LLM wrapper.

Supports:
- real OpenAI (OPENAI_API_KEY)
- mock/stub mode (MOCK_LLM=1) so tests can run without network/keys.

Important: This project NEVER reads keys from source code.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any

from .settings import Settings

@dataclass
class LLM:
    settings: Settings

    def _mock(self, role: str, task: str, payload: dict[str, Any]) -> str:
        # Deterministic simple behavior for demos/tests.
        if role == "extractor":
            text = payload.get("text", "")
            # naive extraction: titlecase words as entities
            words = [w.strip(".,!?()[]{}\"'") for w in text.split()]
            ents = sorted({w for w in words if w[:1].isupper() and len(w) > 2})
            if not ents:
                ents = ["Unknown"]
            return "\n".join([f"- {e} (Entity)" for e in ents])
        if role == "judge":
            return "PASS\nNotes: mock judge; no factual verification performed."
        return f"Mock response for task={task}."

    def chat(self, *, system: str, user: str) -> str:
        if self.settings.mock_llm:
            return self._mock("worker", "chat", {"text": user})

        if not self.settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not set. Set it or run with MOCK_LLM=1")

        from openai import OpenAI

        client = OpenAI(api_key=self.settings.openai_api_key)
        resp = client.chat.completions.create(
            model=self.settings.openai_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content or ""

    def extract_entities(self, text: str) -> list[dict[str, str]]:
        if self.settings.mock_llm:
            out = self._mock("extractor", "extract", {"text": text})
        else:
            out = self.chat(
                system=(
                    "You extract entities from text for a personal assistant memory graph. "
                    "Return a bullet list; each line: '- <name> (<type>)'. Types: Person, Project, Goal, Task, Tool, Org, Place. "
                    "Only include entities explicitly mentioned."
                ),
                user=text,
            )

        entities: list[dict[str, str]] = []
        for line in out.splitlines():
            line = line.strip()
            if not line.startswith("-"):
                continue
            body = line[1:].strip()
            if "(" in body and body.endswith(")"):
                name, typ = body.rsplit("(", 1)
                entities.append({"name": name.strip(), "type": typ[:-1].strip()})
            else:
                entities.append({"name": body, "type": "Entity"})
        # de-dupe
        seen = set()
        uniq = []
        for e in entities:
            k = (e["name"].lower(), e["type"].lower())
            if k in seen:
                continue
            seen.add(k)
            uniq.append(e)
        return uniq

    def judge(self, *, goal: str, answer: str, context: str) -> str:
        if self.settings.mock_llm:
            return self._mock("judge", "judge", {"goal": goal, "answer": answer, "context": context})
        return self.chat(
            system=(
                "You are a strict verifier. Decide if the ANSWER satisfies the GOAL using only CONTEXT. "
                "Output exactly: PASS or FAIL on first line. Then short notes." 
                "If FAIL, list what to fix."
            ),
            user=f"GOAL:\n{goal}\n\nCONTEXT:\n{context}\n\nANSWER:\n{answer}\n",
        )
