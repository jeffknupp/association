# Replay validation: 2025 regular season from 2025-01-01

741 games replayed, 200 simulations each from seed 7; 5 refused. 3.7 s a game.
Baselines fitted on 2946 earlier window games: home-win rate 0.559; strength model p = sigmoid(0.257 + 0.1153 * net-rating difference).

## Win probability (Brier score, lower is better; 0.25 is a coin)

| model | games | Brier |
| --- | --- | --- |
| simulation | 741 | 0.2248 |
| home-win rate | 741 | 0.2488 |
| team strength (net rating) | 741 | 0.2254 (simulation on the same games 0.2248) |
| ESPN pre-game | 741 | 0.1982 (simulation on the same games 0.2248) |

Real home-win rate in the set: 0.540.

## Margin and total

Margin (home minus away): mean error +0.40; real sd 16.4, simulated sd 16.7.
Total: mean error +0.89; real sd 19.5, simulated sd 17.2.

Margin PIT histogram (share per decile; flat is right, a U means the spread is too narrow):

| 0-10 | 10-20 | 20-30 | 30-40 | 40-50 | 50-60 | 60-70 | 70-80 | 80-90 | 90-100 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.090 | 0.084 | 0.101 | 0.126 | 0.103 | 0.107 | 0.104 | 0.127 | 0.085 | 0.074 |

## League rates over the set (per team-game)

| rate | real | simulated |
| --- | --- | --- |
| possessions (box formula) | 100.817 | 101.376 |
| points per 100 possessions | 113.428 | 113.244 |
| 3PA per FGA | 0.419 | 0.415 |
| FTA per FGA | 0.240 | 0.247 |
| offensive rebound rate | 0.255 | 0.245 |
| turnovers per possession | 0.130 | 0.131 |

## Players

15710 player-games. 80% interval coverage: points 0.742, rebounds 0.786, assists 0.857 (close to 0.800 is right).
Minutes: simulated mean minus real, mean -0.03, mean absolute 5.26.

## Refused

- 401705197: EngineError: seed 65: team 20 has only 4 players available in period 4; the engine does not play four
- 401705513: EngineError: seed 141: team 28 has only 4 players available in period 4; the engine does not play four
- 401705744: SimulationRefused: 28's roster has 7 players; 8 are needed
- 401705756: SimulationRefused: 27's roster has 7 players; 8 are needed
- 401705763: EngineError: seed 86: team 28 has only 4 players available in period 4; the engine does not play four
