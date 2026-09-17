"""association - a single CLI for the ESPN NBA dataset: fetch it, audit it, query it.

The commands live in :mod:`association.cli.commands`; the defaults they and the
maintenance scripts share for where the warehouse and Parquet tree are, in
:mod:`association.cli.paths`. ``association.cli:main`` is the console entry
point and ``association.cli:cli`` the Click group, as they were when this was a
single module.

.. versionchanged:: 3.0.0
   A package rather than a module, with the commands in
   :mod:`association.cli.commands` and ``association.repo_paths`` moved to
   :mod:`association.cli.paths`.
"""

from .commands import cli, main

__all__ = ["cli", "main"]
