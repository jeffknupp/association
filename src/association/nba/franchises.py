"""The names a franchise has played under, and which season each belonged to.

ESPN keys every team by FRANCHISE, not by name - id 17 is the Nets whether they
played in New Jersey or Brooklyn - and its teams endpoint holds only today's 30
names. So every game is filed under the right team, and every name printed from
``teams`` is today's: a 2005 game log read "vs BKN" for the New Jersey Nets, and
"Hornets record 2008" answered about the wrong franchise entirely (see
``DATA.md``, "`teams` holds only the 30 current franchises").

A neutral module rather than part of ``query``, because both packages need it:
``fetch/warehouse.py`` names teams in the ``player_game_log`` view, and the
query templates name them everywhere else. Same reason ``season.py`` exists.

.. versionadded:: 2.2.0

.. versionchanged:: 3.0.0
   Moved from ``association.franchises``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FranchiseEra:
    """One name and abbreviation a franchise played under, and the seasons it
    held them. ``last_season`` is None for the name it holds today.

    .. versionadded:: 2.2.0
    """

    team_id: str
    name: str
    abbreviation: str
    first_season: int
    last_season: int | None = None

    def covers(self, season: int) -> bool:
        """Whether the franchise went by this name in ``season``."""
        return self.first_season <= season and (self.last_season is None or season <= self.last_season)


FRANCHISE_ERAS: tuple[FranchiseEra, ...] = (
    FranchiseEra("17", "New Jersey Nets", "NJ", 1978, 2012),
    FranchiseEra("17", "Brooklyn Nets", "BKN", 2013),
    FranchiseEra("3", "Charlotte Hornets", "CHA", 1989, 2002),
    FranchiseEra("3", "New Orleans Hornets", "NO", 2003, 2013),
    FranchiseEra("3", "New Orleans Pelicans", "NO", 2014),
    FranchiseEra("30", "Charlotte Bobcats", "CHA", 2005, 2014),
    FranchiseEra("30", "Charlotte Hornets", "CHA", 2015),
    FranchiseEra("25", "Seattle SuperSonics", "SEA", 1968, 2008),
    FranchiseEra("25", "Oklahoma City Thunder", "OKC", 2009),
    FranchiseEra("29", "Vancouver Grizzlies", "VAN", 1996, 2001),
    FranchiseEra("29", "Memphis Grizzlies", "MEM", 2002),
    FranchiseEra("27", "Washington Bullets", "WSH", 1975, 1997),
    FranchiseEra("27", "Washington Wizards", "WSH", 1998),
)
"""Every name the franchises that were renamed or relocated have played under.

Only franchises whose name CHANGED inside the warehouse's range are listed;
every other team is fully described by ``teams``. Seasons are named by the year
they end.

**Checked against the warehouse where it can be.** The relocations show in the
city each franchise's home games were played in: id 17 in East Rutherford and
Newark, then Brooklyn from 2013; id 25 in Seattle, then Oklahoma City from
2009; id 3 in Charlotte, New Orleans from 2003 (Oklahoma City in 2006 and
2007) and New Orleans again; id 30 first appears in 2005 and id 29 in 1996, the
expansion years. The three pure renames - Bullets to Wizards, Hornets to
Pelicans, Bobcats to Hornets - moved no arena, so no column records them; those
boundaries are league history.

.. versionadded:: 2.2.0
"""

_COLUMNS = ("display_name", "abbreviation")


def _today(team_id: str) -> FranchiseEra | None:
    return next((era for era in FRANCHISE_ERAS if era.team_id == team_id and era.last_season is None), None)


def _value(era: FranchiseEra, column: str) -> str:
    return era.name if column == "display_name" else era.abbreviation


def era_of(team_id: str, season: int) -> FranchiseEra | None:
    """The name franchise ``team_id`` carried in ``season``, if it was renamed.

    .. versionadded:: 2.2.0
    """
    return next((era for era in FRANCHISE_ERAS if era.team_id == team_id and era.covers(season)), None)


def season_name(team_id: str, season: int | None, current: str, column: str = "display_name") -> str:
    """A team's ``display_name`` or ``abbreviation`` as it was in ``season``.

    ``current`` is the value ``teams`` holds today, and it is also the guard:
    the name is changed only when ``current`` is what :data:`FRANCHISE_ERAS`
    says that id is called today. Keying on the id alone renamed whatever a
    table filed under "3" - a test warehouse's Detroit Pistons came back as the
    New Orleans Pelicans.

    .. versionadded:: 2.2.0
    """
    if column not in _COLUMNS:
        raise ValueError(f"season_name reads display_name or abbreviation, not {column!r}")
    today = _today(str(team_id))
    if season is None or today is None or _value(today, column) != current:
        return current
    era = era_of(str(team_id), season)
    return _value(era, column) if era is not None else current


def season_name_sql(team_id: str, season: str, current: str, column: str = "display_name") -> str:
    """:func:`season_name` as a SQL expression, for a name read inside a query.

    All three arguments are SQL expressions - normally column references such
    as ``"opp.team_id"``, ``"tbs.season"`` and ``"opp.display_name"`` - so each
    row is named for its OWN season, which is what a career log that spans a
    relocation needs. Every literal comes from :data:`FRANCHISE_ERAS`, never from
    a question, and quotes are escaped regardless.

    .. versionadded:: 2.2.0
    """
    if column not in _COLUMNS:
        raise ValueError(f"season_name_sql reads display_name or abbreviation, not {column!r}")

    def quoted(text: str) -> str:
        """``text`` as a SQL string literal."""
        return "'" + text.replace("'", "''") + "'"

    whens = []
    for era in FRANCHISE_ERAS:
        today = _today(era.team_id)
        if today is None or era is today:
            continue
        upper = f" AND {season} <= {era.last_season}" if era.last_season is not None else ""
        whens.append(
            f"WHEN CAST({team_id} AS VARCHAR) = {quoted(era.team_id)} AND {current} = {quoted(_value(today, column))} AND {season} >= {era.first_season}{upper} THEN {quoted(_value(era, column))}"
        )
    return f"(CASE {' '.join(whens)} ELSE {current} END)"
