"""Interactive REPL for the ai subcommand."""

from __future__ import annotations

from .agent import Agent


def run_repl(agent: Agent) -> None:
    """Read-eval-print loop for ``association ai``.

    The agent keeps conversation state across questions, so a follow-up can refer
    back to the previous one. ``/reset`` clears it; ``/exit`` quits.
    """
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
