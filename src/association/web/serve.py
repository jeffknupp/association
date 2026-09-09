"""Binding a port and running the server.

The socket is created here rather than left to uvicorn so the real port is
known before anything is served: the default is an *ephemeral* port, and a URL
printed after the fact would be a URL printed after the browser needed it.

.. versionadded:: 2.0.0
"""

from __future__ import annotations

import socket
from pathlib import Path

from ..query.agent import Agent
from ..query.history import DEFAULT_HISTORY_DIR
from .app import INDEX_HTML, create_app
from .runner import AgentRunner, discard

INSTALL_HINT = "The web interface needs extra packages. Install them with:\n\n    pip install 'association[web]'\n"
"""What to say when ``fastapi``/``uvicorn`` are missing.

They are an optional extra so the core install stays small, which means a
missing one is an ordinary configuration state and not a bug - so it gets a
sentence, not an ImportError traceback.

.. versionadded:: 2.0.0
"""


def bind(host: str, port: int) -> socket.socket:
    """A listening socket on ``host``. Port 0 means "any free one".

    .. versionadded:: 2.0.0
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(128)
    return sock


def url_for(sock: socket.socket) -> str:
    """The address to hand a browser, with the port actually bound.

    0.0.0.0 is printed as 127.0.0.1: it is what the socket says, but not
    something a browser can open.

    .. versionadded:: 2.0.0
    """
    host, port = sock.getsockname()[:2]
    return f"http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{port}"


def serve(
    host: str,
    port: int,
    db_path: str,
    out_dir: Path,
    model: str,
    router_model: str,
    history_dir: Path = DEFAULT_HISTORY_DIR,
) -> None:
    """Run the web interface until interrupted.

    Raises:
        SystemExit: the ``web`` extra is not installed, or the page it serves
            is missing from the installed package.

    .. versionadded:: 2.0.0
    """
    try:
        import uvicorn
    except ImportError:
        raise SystemExit(INSTALL_HINT) from None

    # Checked at startup, not on the first request: a wheel that dropped the
    # page would otherwise look like a working server that serves a 404.
    if not INDEX_HTML.exists():
        raise SystemExit(f"Error: the web interface's page is missing from the installed package ({INDEX_HTML}).")

    # verbose=True with a discarding sink, which reads backwards but is right:
    # `verbose` is what makes the engine emit a live trace at all, and `trace`
    # is where it goes. AgentRunner swaps in the requesting stream's sink for
    # the duration of each question, so the default here is only what happens
    # to lines nobody asked for.
    agent = Agent(model, db_path, out_dir, verbose=True, history_dir=history_dir, router_model=router_model, trace=discard)
    runner = AgentRunner(agent)
    app = create_app(runner, db_path=db_path, out_dir=out_dir, model=model, router_model=router_model)

    sock = bind(host, port)
    # flush=True because the URL is the entire point of an ephemeral port, and
    # stdout is block-buffered whenever this is not a terminal - piped into a
    # log or a pane, the line would not appear until the server exited.
    print(f"association is serving at {url_for(sock)}  (ctrl-c to stop)", flush=True)
    uvicorn.Server(uvicorn.Config(app, log_level="warning")).run(sockets=[sock])
