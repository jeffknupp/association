"""A local web interface over the query pipeline.

Served by ``association web``: a chat-shaped page over the same parser →
template → answer path the CLI uses, a refusal naming why where nothing reads
the question. It binds to localhost, has no authentication and is not meant to
be reachable by anything but the person who started it.

Everything here is optional. ``fastapi`` and ``uvicorn`` live in the
``association[web]`` extra so the core install stays at eight dependencies;
``association web`` says how to install them rather than raising ImportError.

.. versionadded:: 2.0.0
"""

from __future__ import annotations
