"""The tools exposed to the local model: schema lookup, read-only SQL, a
parameterized leaderboard for the common "top N players by X" question shape,
and shot chart rendering."""

from __future__ import annotations

import json
from difflib import get_close_matches
from pathlib import Path
from typing import Any

import duckdb

from .court import render_court_html
from .metrics import EXTRA_FIELD_COLUMNS, LEADERBOARD_METRICS, SEASON_TYPE_LABELS, current_season
from .prompt import KNOWN_TABLES

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
        """A "top N players by X" query for a fixed, known set of metrics, with
        every correctness rule this schema needs applied here in code rather
        than left to the model to remember and re-derive per query: the season
        defaults to the CURRENT one (see current_season()) rather than
        whichever season happens to have data loaded; a qualifying minimum
        sample (games or minutes, per metric) is applied by default for
        rate/percentage metrics, since those - confirmed live - let a tiny
        sample (a garbage-time cameo, a 1-game call-up) swing to an extreme
        value no sustained role reaches; a traded player is deduplicated to
        one row. `team` and `fields` are deliberately narrow (a resolved team
        filter and a fixed whitelist of extra box-score columns) rather than a
        free-text filter/column passthrough, which would just reopen the SQL-
        generation reliability problem this tool exists to close. Prefer this
        over hand-writing SQL for any of the metrics in LEADERBOARD_METRICS -
        use run_sql instead only for a metric this tool doesn't cover, or a
        need (a specific opponent, a per-game join) beyond team/fields here."""
        spec = LEADERBOARD_METRICS.get(metric)
        if spec is None:
            # A close-match suggestion (e.g. "points" -> "avg_points") keeps a
            # wrong guess a one-turn fix - confirmed live, without it a wrong
            # metric name sent the model on an unrelated multi-turn detour
            # that eventually recovered but dropped the team/fields it had
            # originally been asked for.
            suggestion = get_close_matches(metric, LEADERBOARD_METRICS, n=1)
            hint = f" Did you mean {suggestion[0]!r}?" if suggestion else ""
            return f"Error: unknown metric {metric!r}.{hint} Known metrics: {sorted(LEADERBOARD_METRICS)}"
        if season_type not in SEASON_TYPE_LABELS:
            return f"Error: season_type must be 1 (preseason), 2 (regular season), or 3 (postseason) - got {season_type!r}."
        unknown_fields = [f for f in fields or [] if f not in EXTRA_FIELD_COLUMNS]
        if unknown_fields:
            return f"Error: unknown field(s) {unknown_fields}. Known fields: {sorted(EXTRA_FIELD_COLUMNS)}"

        resolved_season = season if season is not None else current_season()
        season_type_value: int | str = SEASON_TYPE_LABELS[season_type] if spec.season_type_is_string else season_type
        effective_min_sample = min_sample if min_sample is not None else spec.default_min_sample

        resolved_team_id: str | None = None
        if team is not None:
            matches = self.con.execute(
                "SELECT team_id, display_name FROM teams WHERE team_id = ? OR abbreviation ILIKE ? OR display_name ILIKE ?",
                [team, team, f"%{team}%"],
            ).fetchall()
            if not matches:
                return f"Error: no team found matching {team!r}."
            if len(matches) > 1:
                return f"Error: {team!r} matches more than one team: {[m[1] for m in matches]}. Be more specific."
            resolved_team_id = matches[0][0]

        # A box-score join, keyed on (athlete_id, season, season_type) and
        # deduped to the season-combined row (same pattern as a traded
        # player's season-total row), is needed for `fields` regardless of
        # which table the ranked metric itself lives in - player_season_stats
        # is the one table every metric can join to this way.
        select_cols = ["p.display_name AS display_name", f"t.{spec.column} AS value"]
        select_cols.extend(f"t.{col}" for col in spec.extra_columns)
        select_cols.extend(f"box.{EXTRA_FIELD_COLUMNS[f]} AS {f}" for f in fields or [])
        from_clause = f"FROM {spec.table} t JOIN players p ON p.athlete_id = t.{spec.id_column}"
        params: list[Any] = []
        if fields:
            from_clause += (
                " LEFT JOIN (SELECT * FROM player_season_stats WHERE season = ? AND season_type = ? "
                "QUALIFY ROW_NUMBER() OVER (PARTITION BY athlete_id ORDER BY (team_id IS NULL) DESC) = 1) box "
                f"ON box.athlete_id = t.{spec.id_column}"
            )
            params.extend([resolved_season, season_type])

        where = [f"t.{spec.season_column} = ?", f"t.{spec.season_type_column} = ?"]
        params.extend([resolved_season, season_type_value])
        if effective_min_sample is not None:
            if spec.min_sample_column is None:
                return f"Error: metric {metric!r} has no minimum-sample column to apply min_sample to."
            where.append(f"t.{spec.min_sample_column} >= ?")
            params.append(effective_min_sample)
        if resolved_team_id is not None:
            # A per-stint EXISTS check, not the deduped `box` join above - a
            # traded player's deduped/combined row has team_id IS NULL, which
            # would wrongly exclude them from every team's roster even though
            # they really did play for one of their stint teams that season.
            where.append(
                f"EXISTS (SELECT 1 FROM player_season_stats pss WHERE pss.athlete_id = t.{spec.id_column} "
                "AND pss.season = ? AND pss.season_type = ? AND pss.team_id = ?)"
            )
            params.extend([resolved_season, season_type, resolved_team_id])
        qualify = ""
        if spec.dedup_traded:
            qualify = "QUALIFY ROW_NUMBER() OVER (PARTITION BY t.athlete_id ORDER BY (t.team_id IS NULL) DESC) = 1"

        sql = f"SELECT {', '.join(select_cols)} {from_clause} WHERE {' AND '.join(where)} {qualify} ORDER BY t.{spec.column} DESC LIMIT ?"
        params.append(limit)
        try:
            cur = self.con.execute(sql, params)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchmany(MAX_ROWS)
        except Exception as exc:  # e.g. the table needs a warehouse flag that wasn't used
            return f"SQL error: {exc}" + (f" (requires: {spec.requires})" if spec.requires else "")
        result = [dict(zip(cols, row, strict=True)) for row in rows]
        return json.dumps(
            {
                "metric": metric,
                "label": spec.label,
                "season": resolved_season,
                "season_type": season_type,
                "min_sample_applied": effective_min_sample,
                "team": team,
                "rows": result,
                "row_count": len(result),
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
        tokens = player_name.split()
        where_clause = " AND ".join(["display_name ILIKE ?"] * len(tokens))
        params = [f"%{t}%" for t in tokens]
        match = self.con.execute(
            f"SELECT athlete_id, display_name FROM players "
            f"WHERE {where_clause} ORDER BY display_name LIMIT 5",
            params,
        ).fetchall()
        if not match:
            return f"No player found matching {player_name!r}."
        athlete_id, resolved_name = match[0]
        ambiguous = [m[1] for m in match[1:]]

        # event_id already uniquely identifies one game - season/season_type would be
        # redundant at best and, if the model guesses either one wrong, silently zero
        # out real results. Ignore them whenever a specific game is requested.
        if event_id is not None:
            season = None
            season_type = None

        where = ["athlete_id = ?"]
        filter_params: list[Any] = [athlete_id]
        if season is not None:
            where.append("season = ?")
            filter_params.append(season)
        if season_type is not None:
            where.append("season_type = ?")
            filter_params.append(season_type)
        if event_id is not None:
            where.append("event_id = ?")
            filter_params.append(event_id)
        if period is not None:
            where.append("period = ?")
            filter_params.append(period)
        if shot_value is not None:
            where.append("points_attempted = ?")
            filter_params.append(shot_value)
        if made_only is not None:
            where.append("made = ?")
            filter_params.append(made_only)

        sql = (
            "SELECT coordinate_x, coordinate_y, made, shot_type, period, clock, event_id "
            f"FROM shot_chart WHERE {' AND '.join(where)} AND coordinate_x IS NOT NULL"
        )
        shots = self.con.execute(sql, filter_params).fetchall()
        if not shots:
            return f"No shots found for {resolved_name} with the given filters."

        made = sum(1 for s in shots if s[2])
        total = len(shots)
        title = resolved_name
        subtitle_parts = []
        if season is not None:
            subtitle_parts.append(f"season {season}")
        if season_type is not None:
            subtitle_parts.append({1: "preseason", 2: "regular season", 3: "postseason"}.get(season_type, str(season_type)))
        if event_id is not None:
            subtitle_parts.append(f"game {event_id}")
        if period is not None:
            subtitle_parts.append({1: "Q1", 2: "Q2", 3: "Q3", 4: "Q4"}.get(period, f"OT{period - 4}"))
        if shot_value is not None:
            subtitle_parts.append({1: "free throws", 2: "2PT attempts", 3: "3PT attempts"}.get(shot_value, f"{shot_value}pt attempts"))
        if made_only is not None:
            subtitle_parts.append("makes only" if made_only else "misses only")
        subtitle = ", ".join(subtitle_parts) or "all games"
        subtitle += f" - {made}/{total} ({made / total:.1%}) shown"

        html = render_court_html(title, subtitle, shots)
        safe_name = "".join(c if c.isalnum() else "_" for c in resolved_name.lower())
        scope = "_".join(
            filter(
                None,
                [
                    str(season) if season else None,
                    str(season_type) if season_type else None,
                    event_id,
                    f"p{period}" if period else None,
                    f"{shot_value}pt" if shot_value else None,
                    ("makes" if made_only else "misses") if made_only is not None else None,
                ],
            )
        )
        fname = f"shotchart_{safe_name}" + (f"_{scope}" if scope else "") + ".html"
        out_path = self.out_dir / fname
        out_path.write_text(html)

        msg = f"Rendered shot chart for {resolved_name} ({made}/{total} made, {made / total:.1%}) to {out_path}"
        if ambiguous:
            msg += f". Note: other players also matched '{player_name}': {ambiguous}"
        return msg
