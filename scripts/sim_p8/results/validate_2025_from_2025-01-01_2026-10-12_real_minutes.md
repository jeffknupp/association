# Replay validation: 2025 regular season from 2025-01-01

738 games replayed, 200 simulations each from seed 7; 8 refused. 3.3 s a game.
Baselines fitted on 2946 earlier window games: home-win rate 0.559; strength model p = sigmoid(0.257 + 0.1153 * net-rating difference).
**Rotation: the real game's lineups, followed by the clock** - a diagnostic of the rotation model's share of the error, not a forecast (minutes are given).

## Win probability (Brier score, lower is better; 0.25 is a coin)

| model | games | Brier |
| --- | --- | --- |
| simulation | 738 | 0.2141 |
| home-win rate | 738 | 0.2487 |
| team strength (net rating) | 738 | 0.2255 (simulation on the same games 0.2141) |
| ESPN pre-game | 738 | 0.1986 (simulation on the same games 0.2141) |

Real home-win rate in the set: 0.541.

## Margin and total

Margin (home minus away): mean error +0.13; real sd 16.4, simulated sd 16.7.
Total: mean error +1.01; real sd 19.5, simulated sd 18.2.

Margin PIT histogram (share per decile; flat is right, a U means the spread is too narrow):

| 0-10 | 10-20 | 20-30 | 30-40 | 40-50 | 50-60 | 60-70 | 70-80 | 80-90 | 90-100 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.076 | 0.077 | 0.112 | 0.111 | 0.111 | 0.134 | 0.111 | 0.111 | 0.087 | 0.069 |

## League rates over the set (per team-game)

| rate | real | simulated |
| --- | --- | --- |
| possessions (box formula) | 100.822 | 101.393 |
| points per 100 possessions | 113.418 | 113.278 |
| 3PA per FGA | 0.419 | 0.419 |
| FTA per FGA | 0.240 | 0.244 |
| offensive rebound rate | 0.255 | 0.247 |
| turnovers per possession | 0.130 | 0.131 |

## Players

15694 player-games. 80% interval coverage: points 0.848, rebounds 0.868, assists 0.895 (close to 0.800 is right).
Minutes: simulated mean minus real, mean -0.05, mean absolute 1.17.

Accuracy: the simulated mean's mean absolute error against the real line, and the CRPS of the whole simulated distribution (lower is better; a point forecast's CRPS is its absolute error), by tier of SIMULATED minutes:

| tier | player-games | points MAE | points CRPS | points coverage | rebounds MAE | rebounds CRPS | assists MAE | assists CRPS | minutes MAE | minutes CRPS |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all | 15694 | 3.75 | 2.62 | 0.848 | 1.63 | 1.13 | 1.22 | 0.83 | 1.17 | 0.86 |
| 30+ simulated minutes | 4792 | 5.16 | 3.69 | 0.808 | 2.09 | 1.46 | 1.73 | 1.21 | 1.40 | 1.06 |
| 15-30 simulated minutes | 6936 | 3.79 | 2.66 | 0.831 | 1.71 | 1.19 | 1.22 | 0.83 | 1.22 | 0.89 |
| under 15 simulated minutes | 3966 | 1.96 | 1.26 | 0.925 | 0.93 | 0.62 | 0.63 | 0.38 | 0.81 | 0.57 |
| the model's top scorer on each side | 1476 | 5.95 | 4.28 | 0.793 | 2.04 | 1.43 | 1.99 | 1.42 | 1.35 | 1.02 |

## Refused

- 401705468: EngineError: seed 184: team 10 has only 4 players available in period 5; the engine does not play four
- 401705513: EngineError: seed 9: team 28 has only 4 players available in period 4; the engine does not play four
- 401705587: EngineError: seed 56: team 21 has only 4 players available in period 4; the engine does not play four
- 401705667: EngineError: seed 35: team 21 has only 4 players available in period 4; the engine does not play four
- 401705744: SimulationRefused: 28's roster has 7 players; 8 are needed
- 401705747: EngineError: seed 173: team 26 has only 4 players available in period 4; the engine does not play four
- 401705756: SimulationRefused: 27's roster has 7 players; 8 are needed
- 401705763: EngineError: seed 19: team 28 has only 4 players available in period 4; the engine does not play four
