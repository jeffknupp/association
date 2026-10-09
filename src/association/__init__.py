"""ESPN NBA statistics: resumable fetch, a DuckDB warehouse, and natural-language query.

The three stages are independent - see the Architecture page. Only
:mod:`association.fetch` touches the network.

The distribution on PyPI is ``association-py``; the import package, the
``association`` command and the repository keep the bare name, which PyPI
refuses as a project name.

.. versionchanged:: 5.0.0
   ``__version__`` reads the installed metadata of ``association-py``.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _installed_version

try:
    __version__: str = _installed_version("association-py")
except PackageNotFoundError:  # pragma: no cover - a source tree with no install
    # Deliberately not a plausible-looking fallback like "0.0.0": a version
    # that reads as real would be reported by `--version` and pasted into bug
    # reports as though it meant something.
    __version__ = "unknown"

__all__ = ["__version__"]
