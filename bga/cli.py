import typer
from rich import print
from dotenv import load_dotenv

from .settings import Settings
from .llm import LLM
from .graph import Graph
from .graph_memory import MemoryGraph
from .orchestrator import Orchestrator

app = typer.Typer(add_completion=False)


def _graph(settings: Settings):
    if settings.graph_backend == "neo4j":
        return Graph(settings)
    return MemoryGraph(settings)


@app.command()
def init_db():
    """Initialize graph schema.

    - GRAPH_BACKEND=neo4j: creates constraints/indexes in Neo4j
    - GRAPH_BACKEND=memory: no-op
    """
    load_dotenv()
    st = Settings()
    g = _graph(st)
    g.ensure_schema()
    print(f"[green]OK[/green] schema ensured (backend={st.graph_backend})")


@app.command()
def ask(text: str, source: str = "cli"):
    """Ask a question; stores entities into the graph and answers using context."""
    load_dotenv()
    st = Settings()
    llm = LLM(st)
    g = _graph(st)
    o = Orchestrator(llm=llm, graph=g)

    out = o.handle(text, source=source)

    print("\n[bold]Entities:[/bold]")
    for e in out["entities"]:
        print(f"- {e['name']} ({e['type']})")

    print("\n[bold]Context pack:[/bold]\n" + (out["context"] or "(empty)"))

    print("\n[bold green]Answer:[/bold green]\n" + out["answer"])
    print("\n[bold]Judge:[/bold]\n" + out["judge"])


if __name__ == "__main__":
    app()
