# Replay validation: 2025 regular season from 2025-01-01

736 games replayed, 200 simulations each from seed 7; 10 refused. 1.2 s a game.
Baselines fitted on 2946 earlier window games: home-win rate 0.559; strength model p = sigmoid(0.257 + 0.1153 * net-rating difference).

## Win probability (Brier score, lower is better; 0.25 is a coin)

| model | games | Brier |
| --- | --- | --- |
| simulation | 736 | 0.2261 |
| home-win rate | 736 | 0.2487 |
| team strength (net rating) | 736 | 0.2242 (simulation on the same games 0.2261) |
| ESPN pre-game | 736 | 0.1979 (simulation on the same games 0.2261) |

Real home-win rate in the set: 0.541.

## Margin and total

Margin (home minus away): mean error +0.53; real sd 16.4, simulated sd 16.7.
Total: mean error -1.94; real sd 19.5, simulated sd 18.6.

Margin PIT histogram (share per decile; flat is right, a U means the spread is too narrow):

| 0-10 | 10-20 | 20-30 | 30-40 | 40-50 | 50-60 | 60-70 | 70-80 | 80-90 | 90-100 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.088 | 0.082 | 0.117 | 0.107 | 0.107 | 0.099 | 0.120 | 0.128 | 0.086 | 0.067 |

## League rates over the set (per team-game)

| rate | real | simulated |
| --- | --- | --- |
| possessions (box formula) | 100.840 | 100.457 |
| points per 100 possessions | 113.501 | 112.969 |
| 3PA per FGA | 0.420 | 0.409 |
| FTA per FGA | 0.240 | 0.250 |
| offensive rebound rate | 0.255 | 0.243 |
| turnovers per possession | 0.130 | 0.132 |

## Players

15624 player-games. 80% interval coverage: points 0.740, rebounds 0.788, assists 0.851 (close to 0.800 is right).
Minutes: simulated mean minus real, mean -0.03, mean absolute 5.26.

## Refused

- 401705049: EngineError: seed 51: team 22 has only 4 players available in period 4; the engine does not play four
- 401705129: EngineError: seed 19: team 1 has only 4 players available in period 5; the engine does not play four
- 401705131: EngineError: seed 126: team 16 has only 4 players available in period 4; the engine does not play four
- 401705275: EngineError: seed 46: team 22 has only 4 players available in period 4; the engine does not play four
- 401705477: EngineError: seed 143: team 18 has only 4 players available in period 4; the engine does not play four
- 401705513: EngineError: seed 39: team 28 has only 4 players available in period 4; the engine does not play four
- 401705744: SimulationRefused: 28's roster has 7 players; 8 are needed
- 401705756: SimulationRefused: 27's roster has 7 players; 8 are needed
- 401705760: EngineError: seed 189: team 29 has only 4 players available in period 4; the engine does not play four
- 401705765: EngineError: seed 127: team 13 has only 4 players available in period 4; the engine does not play four
