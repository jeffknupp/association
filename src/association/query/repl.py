"""Interactive REPL for the ai subcommand."""

from __future__ import annotations

from .agent import Agent


def run_repl(agent: Agent) -> None:
    print(f"NBA query REPL (model: {agent.model}). Type a question, or /reset, /exit.")
    while True:
        try:
            question = input("nba> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not question:
            continue
        if question in ("/exit", "/quit"):
            break
        if question == "/reset":
            agent.reset()
            print("(conversation reset)")
            continue
        answer = agent.ask(question)
        print(answer)
