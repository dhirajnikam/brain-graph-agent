from pydantic import BaseModel
import os

class Settings(BaseModel):
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    # Graph backend: "neo4j" (bolt), "sqlite" (local persistent), or "memory" (in-process demo backend)
    graph_backend: str = os.getenv("GRAPH_BACKEND", "sqlite")

    neo4j_uri: str = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user: str = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "neo4jpassword")

    # If true, don't call OpenAI; use deterministic stub.
    mock_llm: bool = os.getenv("MOCK_LLM", "0") == "1"
