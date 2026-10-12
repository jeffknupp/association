# Replay validation: 2025 regular season from 2025-01-01

734 games replayed, 200 simulations each from seed 7; 12 refused. 3.5 s a game.
Baselines fitted on 2946 earlier window games: home-win rate 0.559; strength model p = sigmoid(0.257 + 0.1153 * net-rating difference).
**Rotation: the real game's lineups, followed by the clock** - a diagnostic of the rotation model's share of the error, not a forecast (minutes are given).

## Win probability (Brier score, lower is better; 0.25 is a coin)

| model | games | Brier |
| --- | --- | --- |
| simulation | 734 | 0.2161 |
| home-win rate | 734 | 0.2490 |
| team strength (net rating) | 734 | 0.2246 (simulation on the same games 0.2161) |
| ESPN pre-game | 734 | 0.1987 (simulation on the same games 0.2161) |

Real home-win rate in the set: 0.538.

## Margin and total

Margin (home minus away): mean error +0.50; real sd 16.4, simulated sd 16.6.
Total: mean error +0.93; real sd 19.5, simulated sd 17.1.

Margin PIT histogram (share per decile; flat is right, a U means the spread is too narrow):

| 0-10 | 10-20 | 20-30 | 30-40 | 40-50 | 50-60 | 60-70 | 70-80 | 80-90 | 90-100 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.084 | 0.086 | 0.113 | 0.109 | 0.101 | 0.125 | 0.124 | 0.105 | 0.094 | 0.059 |

## League rates over the set (per team-game)

| rate | real | simulated |
| --- | --- | --- |
| possessions (box formula) | 100.844 | 101.395 |
| points per 100 possessions | 113.468 | 113.309 |
| 3PA per FGA | 0.419 | 0.414 |
| FTA per FGA | 0.241 | 0.248 |
| offensive rebound rate | 0.255 | 0.245 |
| turnovers per possession | 0.130 | 0.131 |

## Players

15614 player-games. 80% interval coverage: points 0.844, rebounds 0.867, assists 0.894 (close to 0.800 is right).
Minutes: simulated mean minus real, mean -0.05, mean absolute 1.15.

## Refused

- 401705049: EngineError: seed 33: team 22 has only 4 players available in period 4; the engine does not play four
- 401705079: EngineError: seed 77: team 17 has only 4 players available in period 4; the engine does not play four
- 401705197: EngineError: seed 49: team 20 has only 4 players available in period 4; the engine does not play four
- 401705513: EngineError: seed 13: team 28 has only 4 players available in period 4; the engine does not play four
- 401705607: EngineError: seed 25: team 20 has only 4 players available in period 4; the engine does not play four
- 401705615: EngineError: seed 39: team 10 has only 4 players available in period 4; the engine does not play four
- 401705703: EngineError: seed 62: team 27 has only 4 players available in period 4; the engine does not play four
- 401705744: SimulationRefused: 28's roster has 7 players; 8 are needed
- 401705756: SimulationRefused: 27's roster has 7 players; 8 are needed
- 401705760: EngineError: seed 39: team 29 has only 4 players available in period 4; the engine does not play four
- 401705763: EngineError: seed 85: team 28 has only 4 players available in period 4; the engine does not play four
- 401705765: EngineError: seed 132: team 13 has only 4 players available in period 4; the engine does not play four
