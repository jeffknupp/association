"""The tools exposed to the local model: schema lookup, read-only SQL, a
parameterized leaderboard for the common "top N players by X" question shape,
and shot chart rendering."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

from .leaderboard import LeaderboardError, run_leaderboard
from .prompt import KNOWN_TABLES
from .shotchart import render_shot_chart

MAX_ROWS = 200

# column name -> (lookup table, id column in that table, name column in that table)
ID_LOOKUPS = {
    "athlete_id": ("players", "athlete_id", "display_name"),
    "team_id": ("teams", "team_id", "display_name"),
    "home_team_id": ("teams", "team_id", "display_name"),
    "away_team_id": ("teams", "team_id", "display_name"),
    "winner_team_id": ("teams", "team_id", "display_name"),
    "opponent_team_id": ("teams", "team_id", "display_name"),
}


class Toolbox:
    def __init__(self, db_path: str, out_dir: Path):
        self.con = duckdb.connect(db_path, read_only=True)
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def _enrich_ids_with_names(self, cols: list[str], rows: list[dict]) -> None:
        """Add a '<col>_name' (or 'athlete_name'/'team_name') field alongside any
        *_id column so the model always has a display name available and never
        has to surface a raw id to the user."""
        if not rows:
            return
        for col in cols:
            lookup = ID_LOOKUPS.get(col)
            if lookup is None:
                continue
            table, id_col, name_col = lookup
            ids = sorted({r[col] for r in rows if r.get(col) is not None})
            if not ids:
                continue
            placeholders = ",".join("?" * len(ids))
            name_map = dict(
                self.con.execute(
                    f"SELECT {id_col}, {name_col} FROM {table} WHERE {id_col} IN ({placeholders})",
                    ids,
                ).fetchall()
            )
            new_key = (col[:-3] if col.endswith("_id") else col) + "_name"
            for r in rows:
                r[new_key] = name_map.get(r.get(col))

    def describe_table(self, table_name: str) -> str:
        if table_name not in KNOWN_TABLES:
            return f"Unknown table {table_name!r}. Known tables: {sorted(KNOWN_TABLES)}"
        rows = self.con.execute(f"DESCRIBE {table_name}").fetchall()
        return json.dumps([{"column": r[0], "type": r[1]} for r in rows])

    def run_sql(self, query: str) -> str:
        q = query.strip().rstrip(";")
        head = q[:10].lstrip().upper()
        if not (head.startswith("SELECT") or head.startswith("WITH")):
            return "Error: only read-only SELECT/WITH queries are allowed."
        try:
            cur = self.con.execute(q)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchmany(MAX_ROWS)
        except Exception as exc:  # let the model see the DB error and retry
            return f"SQL error: {exc}"
        result = [dict(zip(cols, row, strict=True)) for row in rows]
        self._enrich_ids_with_names(cols, result)
        truncated = len(result) == MAX_ROWS
        return json.dumps({"rows": result, "row_count": len(result), "truncated": truncated}, default=str)

    def get_leaderboard(
        self,
        metric: str,
        season: int | None = None,
        season_type: int = 2,
        min_sample: int | None = None,
        team: str | None = None,
        fields: list[str] | None = None,
        limit: int = 10,
    ) -> str:
        """The agent-tool face of run_leaderboard: same query, JSON out, and a
        LeaderboardError's message returned verbatim as the tool result so the
        model can read the error and correct itself. The fast-path template
        calls run_leaderboard directly - one implementation, two callers."""
        try:
            result = run_leaderboard(
                self.con, metric, season=season, season_type=season_type, min_sample=min_sample, team=team, fields=fields, limit=limit
            )
        except LeaderboardError as exc:
            return str(exc)
        return json.dumps(
            {
                "metric": result.metric,
                "label": result.label,
                "season": result.season,
                "season_type": result.season_type,
                "min_sample_applied": result.min_sample_applied,
                "team": result.team,
                "rows": result.rows,
                "row_count": len(result.rows),
            },
            default=str,
        )

    def render_shot_chart(
        self,
        player_name: str,
        season: int | None = None,
        season_type: int | None = None,
        event_id: str | None = None,
        period: int | None = None,
        shot_value: int | None = None,
        made_only: bool | None = None,
    ) -> str:
        """The agent-tool face of shotchart.render_shot_chart - the fast-path
        template calls the same function with the same connection and out_dir."""
        return render_shot_chart(
            self.con,
            self.out_dir,
            player_name,
            season=season,
            season_type=season_type,
            event_id=event_id,
            period=period,
            shot_value=shot_value,
            made_only=made_only,
        )
