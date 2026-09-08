Installation
============

From PyPI
---------

.. code-block:: console

   $ pip install association

Or, to get the CLI on your PATH without adding it to a project environment:

.. code-block:: console

   $ uv tool install association

Python 3.10 or newer is required. The package is pure Python and ships no
compiled extensions, so there is a single wheel for every platform.

The models
----------

Installing the package is not sufficient on its own: the query interface talks
to a local `Ollama <https://ollama.com>`_ server, and two models must be pulled
before :doc:`query <usage>` will answer anything.

.. code-block:: console

   $ ollama serve &
   $ ollama pull qwen2.5:3b    # router, the fast path - required, ~1.9GB
   $ ollama pull qwen2.5:7b    # fall-through agent - required, ~4.7GB
   $ ollama pull qwen3:8b      # optional, for --think, ~5.2GB

Both required models stay resident together in about 7GB. See
:doc:`architecture` for why the work is split across two models of very
different sizes, and :doc:`usage` for what each one does.

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
   $ uv sync --extra dev --extra docs
   $ uv run pre-commit install

``uv sync`` installs the package in editable mode along with the linting,
typing, test and documentation toolchain. See :doc:`releasing` for how a
version reaches PyPI.
