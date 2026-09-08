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
    "myst_parser",
]

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
html_static_path = []
