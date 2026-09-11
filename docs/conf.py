"""Sphinx configuration for the association documentation.

Every module under ``src/association`` is documented, generated recursively by
``autosummary`` rather than by checked-in stub files, so a new module appears in
the API reference without anyone remembering to add it.
"""

from __future__ import annotations

import sys
from datetime import date
from importlib.metadata import version as installed_version
from pathlib import Path
from typing import TYPE_CHECKING

import sphinx_autodoc_typehints
from packaging.version import Version

if TYPE_CHECKING:
    from sphinx.application import Sphinx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

project = "association"
author = "Jeff Knupp"
copyright = f"{date.today().year}, {author}"

# Read the version from installed package metadata rather than repeating the
# literal here. pyproject.toml is the single source of truth - the publish
# workflow parses it to check the tag agrees - and a hand-copied number in this
# file would be the obvious thing to forget on a bump.
#
# Read through importlib.metadata rather than `from association import
# __version__ as release`: that spelling reads as an unused import to the
# linter, which removes it and leaves `release` silently undefined - Sphinx
# treats it as optional and builds happily with an empty version.
release = installed_version("association")

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinx_autodoc_typehints",
    "sphinx_click",
    "sphinx_copybutton",
    "myst_parser",
]

# Strip the "$ " shell prompt so a copied command is ready to paste, not ready
# to fail on a stray leading dollar sign.
copybutton_prompt_text = r"\$ "
copybutton_prompt_is_regexp = True

templates_path = ["_templates"]
exclude_patterns = ["_build"]

# Recursive autosummary is what makes "document everything" hold over time: the
# stub pages under api/ are generated at build time from the package tree.
autosummary_generate = True
autodoc_default_options = {
    "members": True,
    "undoc-members": True,
    "show-inheritance": True,
    "member-order": "bysource",
}
# Module docstrings in this project carry the design rationale; class
# docstrings carry the invariants. Keep both rather than merging.
autoclass_content = "both"
autodoc_typehints = "description"
typehints_use_signature = False
napoleon_google_docstring = True
napoleon_numpy_docstring = False

_upstream_rtype_insert_index = sphinx_autodoc_typehints.get_insert_index


def _rtype_insert_index(app: Sphinx, lines: list[str]) -> sphinx_autodoc_typehints.InsertIndexInfo | None:
    """Never put ``:rtype:`` directly under a line of prose.

    A docstring with no ``Args:`` or ``Returns:`` section gets its ``:rtype:``
    inserted just above the first block that is not a paragraph - here, 66
    times out of 68, the ``.. versionadded::`` the conventions put last.
    sphinx-autodoc-typehints before 3.2.0 inserts it with no blank line in
    front, so reStructuredText reads the field as one more line of the
    paragraph above: 68 literal ``:rtype: list[str]`` strings across 18 API
    pages, and no warning for ``-W`` to fail on.

    This is 3.2.0's own fix, backported: when the line above the insertion
    point holds text, put the field at the end of the docstring instead. Not
    the upgrade itself, because 3.2.0 needs Sphinx 8.2 and Python 3.11 and the
    docs extra still resolves for 3.10. And not a blank line in front of the
    field where it landed: autodoc adds the Parameters to whatever field list
    a docstring already has, and makes its own at the end only when there is
    none, so a Return type left above a bullet list would pull the Parameters
    up there with it. At the end, it joins them where they already render.
    """
    found = _upstream_rtype_insert_index(app, lines)
    if found is not None and found.found_directive and found.insert_index and lines[found.insert_index - 1]:
        return sphinx_autodoc_typehints.InsertIndexInfo(insert_index=len(lines))
    return found


# Gated on the version, so the shim stops applying the moment the lock moves to
# a release that carries the fix itself - delete it then.
if Version(installed_version("sphinx-autodoc-typehints")) < Version("3.2.0"):
    sphinx_autodoc_typehints.get_insert_index = _rtype_insert_index

intersphinx_mapping = {"python": ("https://docs.python.org/3", None)}
# Fetched at build time; a network hiccup should not fail a commit.
intersphinx_disabled_reftypes = ["*"]

myst_enable_extensions = ["colon_fence", "deflist"]
myst_heading_anchors = 3

html_theme = "furo"
# Includes the version: Furo shows this in the sidebar, and an explicit
# html_title otherwise suppresses the project-and-version line it would
# render by default, leaving a docs site that never states what it documents.
html_title = f"association {release}"
html_static_path = ["_static"]


# Headings whose command list gets its own code block (and copy button) per
# line, rather than one shared block for the whole list - "Examples" and the
# ollama setup steps are each independent commands a reader runs one at a
# time, not a script to paste as a unit.
_ONE_BLOCK_PER_LINE_HEADINGS = {"Examples:", "Setup for query/ai (one-time):"}


def _epilog_commands_as_code_blocks(app: object, ctx: object, lines: list[str]) -> None:
    """Render the CLI epilog's ``\\b``-marked command lists as real code blocks.

    sphinx-click turns every ``\\b``-preserved paragraph into an RST line
    block (``| some text``), which keeps line breaks but renders as plain
    text - no monospacing, no highlighting, no copy button. The command
    examples in :data:`association.cli.CLI_EPILOG` want the same treatment as
    every other command example in these docs, so runs of line-block text are
    rewritten here into ``.. code-block:: console`` before Sphinx parses them.
    """
    rewritten: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("| "):
            block: list[str] = []
            while i < len(lines) and lines[i].startswith("| "):
                block.append(lines[i][2:])
                i += 1
            heading = next((entry for entry in reversed(rewritten) if entry), "")
            if heading in _ONE_BLOCK_PER_LINE_HEADINGS:
                for entry in block:
                    rewritten.append(".. code-block:: console")
                    rewritten.append("")
                    rewritten.append("   " + entry)
                    rewritten.append("")
            else:
                rewritten.append(".. code-block:: console")
                rewritten.append("")
                rewritten.extend("   " + entry for entry in block)
                rewritten.append("")
        else:
            rewritten.append(line)
            i += 1
    lines[:] = rewritten


def setup(app: object) -> None:
    """Register documentation-build-time hooks."""
    app.connect("sphinx-click-process-epilog", _epilog_commands_as_code_blocks)  # type: ignore[attr-defined]
