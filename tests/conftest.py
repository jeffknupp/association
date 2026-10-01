"""Suite-wide test configuration: the day the tests are run "on".

Fifteen tests encode the 2025-26 season - a fixture's game dates beside
``current_season()``, an expected ISO date, a sentence naming "2026" - and
call the reader or a template directly, where the season is the calendar's.
Run on or after 2026-10-01 they failed for a reason that is not the code:
the calendar's season had turned over (measured 2026-09-30 with the date
pinned to October 5: 15 failed, 2,373 passed). The suite is a fixed world,
so its "today" is pinned here, once. A test about another day sets
``ASSOCIATION_TODAY`` itself (``monkeypatch.setenv``), and an explicit value
in the environment wins, so the suite can still be run "on" any date by
hand.
"""

from __future__ import annotations

import os

from association.nba.season import TODAY_ENV

os.environ.setdefault(TODAY_ENV, "2026-09-30")
