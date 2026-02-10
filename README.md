# brain-graph-agent

Minimal working example of:
- **Graph memory** in **Neo4j**
- An **orchestrator** LLM (OpenAI) that pulls a **context pack** from the graph
- A **judge/verifier** step
- **Write-back**: entities mentioned in the user message get stored in Neo4j with provenance.

## 0) Security

- Never hardcode keys.
- Use `OPENAI_API_KEY` via environment variables.
- If you already pasted an API key into chat/logs, **revoke it** in the OpenAI dashboard.

## 1) Graph backend

This repo works in two modes:

- **Memory graph (default)**: no external dependencies.
- **Neo4j graph**: connect to a running Neo4j over Bolt.

### Neo4j (optional)
If you have Docker installed, you can run Neo4j like this:

```bash
docker compose up -d
```

Neo4j UI: http://localhost:7474 (user: `neo4j`, pass: `neo4jpassword`)

If you don’t have Docker, you can still run the demo with `GRAPH_BACKEND=memory`.

## 2) Install & run (local)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

cp .env.example .env
# edit .env and set OPENAI_API_KEY (or set MOCK_LLM=1 for offline test)

bga init-db
bga ask "Dhiraj wants to catch up with Jay. We use OpenClaw on WhatsApp." --source "demo:1"
```

### Offline test (no OpenAI key)

```bash
MOCK_LLM=1 bga ask "Dhiraj wants to catch up with Jay" --source "demo:mock"
```

## How it works

1) **Extractor**: LLM extracts entities from input.
2) **Graph write**: `Entity(name,type)` nodes are upserted, connected to a `Source(id)` node.
3) **Context pack**: query Neo4j for latest entities and sources.
4) **Worker**: LLM answers the user using only this context.
5) **Judge**: verifies the answer doesn’t invent facts.

This is the smallest end-to-end skeleton you can extend with:
- typed nodes (Person/Project/Goal/Task)
- vector search for long docs
- multiple workers
- stronger judge gates for write-back
