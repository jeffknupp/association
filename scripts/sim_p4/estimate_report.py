"""P4-box acceptance: run `rates.estimate` on the real files for the 2024-2026 window as of the day
after the last 2026 regular-season game, for the Thunder and the Celtics, and print the timing, the
league parameters, every fitted strength, three players (a star, a bench player, a near-prior one)
and the sanity checks against known league figures.

    PYTHONPATH=src .venv/bin/python scripts/sim_p4/estimate_report.py SIM_DUCKDB NBA_DUCKDB
"""
import sys, time
from pathlib import Path
from association.nba.season import eastern_date_sql
from association.sim import database, rates
from association.sim.params import MIN_TRIALS, ZONES, Window

THUNDER, CELTICS = "25", "2"

con = database.connect(Path(sys.argv[1]), Path(sys.argv[2]), read_only=True)
as_of = rates.as_of_after(con, 2026)
window = Window(seasons=(2024, 2025, 2026), as_of=as_of)
print(f"window: {window.describe()} (as_of is the day after the last 2026 regular-season game in real_games, Eastern)")
games, player_seasons, team_seasons = con.execute(f"""
    WITH g AS (SELECT * FROM nba.real_games WHERE season IN (2024, 2025, 2026) AND {eastern_date_sql('date')} < DATE '{as_of.isoformat()}')
    SELECT (SELECT count(*) FROM g),
           (SELECT count(*) FROM (SELECT DISTINCT b.athlete_id, b.season FROM nba.player_box_stats_deduped b JOIN g USING (event_id, season) WHERE b.minutes IS NOT NULL)),
           (SELECT count(*) FROM (SELECT DISTINCT team_id, season FROM (SELECT unnest([home_team_id, away_team_id]) AS team_id, season FROM g)))
""").fetchone()
print(f"population: {games} games, {player_seasons} player-seasons with minutes, {team_seasons} team-seasons")

t = time.time()
est = rates.estimate(con, window, team_ids=[THUNDER, CELTICS])
elapsed = time.time() - t
print(f"estimate: {elapsed:.1f}s for {len(est.players)} players on 2 teams, {len(est.missing)} missing ({sorted(est.missing)})")
t = time.time()
everyone = rates.estimate(con, window)
print(f"estimate (every team): {time.time() - t:.1f}s for {len(everyone.players)} players, {len(everyone.teams)} teams, {len(everyone.missing)} missing")

L = est.league
print("\nleague")
print(f"  pace {L.pace:.2f} possessions per 48 team-minutes   home_court {L.home_court:+.2f} points")
print(f"  turnover_rate {L.turnover_rate:.4f}/poss   foul_rate (trips) {L.foul_rate:.4f}/poss   offensive_rebound_rate {L.offensive_rebound_rate:.4f}")
print(f"  free_throw_rate {L.free_throw_rate:.4f}   and_one_rate {L.and_one_rate:.4f}   assisted_rate {L.assisted_rate:.4f}   live_ball_share {L.live_ball_share:.4f}")
print("  zone_mix  " + "  ".join(f"{z} {L.zone_mix[z]:.4f}" for z in ZONES))
print("  make_rate " + "  ".join(f"{z} {L.make_rate[z]:.4f}" for z in ZONES))
print("  per_minute " + "  ".join(f"{k.replace('_per_minute', '')} {v:.4f}" for k, v in L.per_minute.items()))

print("\nfitted strengths (k, pseudo-trials of the rate's own unit)")
for name, k in sorted(L.strengths.items()):
    unit = "minutes" if name in rates.PER_MINUTE_RATES else "games of 48 team-minutes" if name == "pace" else "possessions" if name in ("forced_turnover_rate", "foul_rate") else "misses" if name == "defensive_rebound_rate" else "makes" if name in ("and_one_rate", "assisted_rate") else "turnovers" if name == "live_ball_share" else "free throws" if name == "free_throw_rate" else "shots"
    print(f"  {name:36s} {k:12.2f}  {unit}")

print("\nteams")
for team in est.teams.values():
    print(f"  {team.name}: pace {team.pace.value:.2f} (raw {team.pace.value:.2f} over {team.pace.trials:.1f} games48)  forced_turnover {team.forced_turnover_rate.value:.4f}  foul {team.foul_rate.value:.4f}  dreb {team.defensive_rebound_rate.value:.4f}")
    print("    allowed make " + "  ".join(f"{z} {team.allowed_make_rate[z].value:.3f} (n={team.allowed_make_rate[z].trials:.0f})" for z in ZONES))
    print("    allowed mix  " + "  ".join(f"{z} {team.allowed_zone_mix.probability(z):.3f}" for z in ZONES))


def show(p):
    print(f"\n  {p.name} ({p.team_id}): {p.minutes:.0f} minutes, {p.zone_mix.trials:.0f} charted shots, near_prior={p.near_prior}")
    for name in ("shots_per_minute", "free_throw_trips_per_minute", "turnovers_per_minute", "assists_per_minute", "offensive_rebounds_per_minute", "defensive_rebounds_per_minute", "steals_per_minute", "blocks_per_minute", "fouls_per_minute"):
        r = getattr(p, name)
        print(f"    {name:32s} {r.value:.4f}  (x36 = {36 * r.value:5.2f}; raw {r.value if r.trials == 0 else (r.value * (r.trials + r.strength) - r.strength * r.prior) / r.trials:.4f}, prior {r.prior:.4f}, k {r.strength:.1f}, prior weight {r.prior_weight:.3f})")
    for name in ("and_one_rate", "assisted_rate", "free_throw_rate", "live_ball_share"):
        r = getattr(p, name)
        print(f"    {name:32s} {r.value:.4f}  (n {r.trials:.0f}, prior {r.prior:.4f}, k {r.strength:.1f}, prior weight {r.prior_weight:.3f})")
    print("    zone_mix  " + "  ".join(f"{z} {p.zone_mix.probability(z):.3f}" for z in ZONES))
    print("    make_rate " + "  ".join(f"{z} {p.make_rate[z].value:.3f} (n={p.make_rate[z].trials:.0f}, w={p.make_rate[z].prior_weight:.2f})" for z in ZONES))


players = sorted(est.players.values(), key=lambda p: -p.minutes)
star = players[0]
bench = next(p for p in players if p.team_id == CELTICS and 500 <= p.minutes <= 2000)
near = next((p for p in players if p.near_prior), None) or next(p for p in sorted(everyone.players.values(), key=lambda p: -p.minutes) if p.near_prior)
print("\nplayers: the star, a bench player, a near-prior player")
for p in (star, bench, near):
    show(p)
print(f"\n  near-prior players: {sum(p.near_prior for p in est.players.values())} of {len(est.players)} on the two teams; {sum(p.near_prior for p in everyone.players.values())} of {len(everyone.players)} league-wide (under {MIN_TRIALS} charted shots)")

print("\nsanity against known figures")
three_share = L.zone_mix["corner_three"] + L.zone_mix["above_break_three"]
print(f"  pace {L.pace:.1f} against ~98-100 (NBA.com's pace, which averages the two sides and uses 0.44 FTA, was 98.8 in 2024-25); the box formula over one side's lines runs about a possession high")
print(f"  free-throw rate {L.free_throw_rate:.3f} against ~0.78")
print(f"  three-point share of charted shots {three_share:.3f} against ~0.40-0.42 of FGA")
print(f"  home margin {L.home_court:+.2f} against ~+2 (the window holds the 2024 and 2025 playoffs too)")
print(f"  turnovers per possession {L.turnover_rate:.3f} against ~0.13-0.14; free-throw trips per possession {L.foul_rate:.3f}; offensive rebound rate {L.offensive_rebound_rate:.3f} against ~0.25-0.27")
