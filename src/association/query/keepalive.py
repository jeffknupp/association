"""How long ollama should keep a model resident between questions.

Not OLLAMA_MAX_LOADED_MODELS: that is read by the ollama SERVER (a systemd unit
here), so setting it from this process would be a no-op, and it is unnecessary
anyway - confirmed live, ollama already holds both the router and agent models
at once (`/api/ps` reports qwen2.5:7b and qwen2.5:3b resident together).

What does bite is the idle unload. ollama drops a model after ~5 minutes, and
reloading the router costs ~15s on this box - paid by whoever asks the first
question after a coffee break. `keep_alive` is a per-request field, so it works
from the client, and holding the 2.2GB router resident is cheap on a 16GB
machine. Override with ASSOCIATION_KEEP_ALIVE (any duration ollama accepts,
e.g. "5m", "2h", or "-1" to keep loaded until evicted).
"""

from __future__ import annotations

import os

KEEP_ALIVE = os.environ.get("ASSOCIATION_KEEP_ALIVE", "30m")
