association
===========

Fetch ESPN's NBA dataset, audit it, and query it in natural language against a
local DuckDB warehouse — no cloud services, no API keys, and no data leaving
the machine.

.. code-block:: console

   $ association data pull --seasons 2024-2026
   $ association data check --seasons 2026
   $ association query "who had the most 30+ point games this season?"
   Luka Doncic had the most games with 30+ points in the 2026 regular season, with
   44. Next: Shai Gilgeous-Alexander (43), Jaylen Brown (35), Donovan Mitchell (34),
   Anthony Edwards (32).

.. toctree::
   :maxdepth: 2
   :caption: Contents

   installation
   usage
   architecture
   commands
   data-sources
   releasing
   changelog
   api/index

Indices
-------

* :ref:`genindex`
* :ref:`modindex`
