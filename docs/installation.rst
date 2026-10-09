Installation
============

From GitHub
-----------

.. note::

   ``pip install association-py`` is the install command from the next
   release on: the distribution is named ``association-py`` on PyPI, since
   PyPI refuses the bare name though nobody holds it (the import package,
   the ``association`` command and the repository keep it). Nothing is on
   PyPI yet, so install from the tag below for now; this section goes back
   to being *From PyPI* once the first version is up.

Install from a release tag:

.. code-block:: console

   $ pip install git+https://github.com/jeffknupp/association@v5.0.0

Or, to get the CLI on your PATH without adding it to a project environment:

.. code-block:: console

   $ uv tool install git+https://github.com/jeffknupp/association@v5.0.0

Releases cut from now on also carry the built wheel and sdist as downloadable
assets, so ``pip install ./association_py-X.Y.Z-py3-none-any.whl`` works from
a local copy (``association-X.Y.Z-...`` for the releases before the rename).
Releases made before that have none attached; use the tag above.

Python 3.14 or newer is required. The package is pure Python and ships no
compiled extensions, so there is a single wheel for every platform.

The models
----------

Installing the package is not sufficient on its own: the query interface talks
to a local `Ollama <https://ollama.com>`_ server, and one model must be pulled
before :doc:`query <usage>` will answer anything.

.. code-block:: console

   $ ollama serve &
   $ ollama pull qwen2.5:3b    # the normalizer - required, ~1.9GB

It stays resident in about 2GB. See :doc:`architecture` for why one small
model is the whole model budget, and :doc:`usage` for what it does.

The data
--------

A fresh install has no data. Nothing is bundled — the warehouse is built on
your machine from ESPN's endpoints, which takes a while for a full season:

.. code-block:: console

   $ association data pull --seasons 2026

See :doc:`usage` for the fetch recipes and :doc:`data-sources` for what is
being called and the rate limits the fetcher holds itself to.

From source
-----------

To work on ``association`` itself:

.. code-block:: console

   $ git clone https://github.com/jeffknupp/association
   $ cd association
   $ uv sync --extra dev --extra docs --extra web
   $ uv run pre-commit install

``uv sync`` installs the package in editable mode along with the linting,
typing, test and documentation toolchain. All three extras are needed even
just to run the tests and gates - the docs build is one of the pre-commit
hooks, and ``tests/web/`` imports ``fastapi`` directly with no skip guard, so
it fails to collect without the ``web`` extra installed. See :doc:`releasing`
for how a version reaches PyPI.
