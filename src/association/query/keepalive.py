"""How long ollama should keep a model resident between questions.

Not OLLAMA_MAX_LOADED_MODELS: that is read by the ollama SERVER (a systemd unit
here), so setting it from this process would be a no-op, and it is unnecessary
anyway - confirmed live while there were two, ollama held both the router's and
the fall-through agent's models at once (`/api/ps` reported qwen2.5:7b and
qwen2.5:3b resident together); since 5.0.0 the normalizer's is the only one.

What does bite is the idle unload. ollama drops a model after ~5 minutes, and
reloading the model costs ~15s on this box - paid by whoever asks the first
question after a coffee break. `keep_alive` is a per-request field, so it works
from the client, and holding the normalizer's 2.2GB model resident is cheap on a 16GB
machine. Override with ASSOCIATION_KEEP_ALIVE (any duration ollama accepts,
e.g. "5m", "2h", or "-1" to keep loaded until evicted).
"""

from __future__ import annotations

import os

KEEP_ALIVE = os.environ.get("ASSOCIATION_KEEP_ALIVE", "30m")
