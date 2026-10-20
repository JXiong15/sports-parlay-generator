# sports-parlay-generator (BetBot)

A personal sports-betting research tool. It pulls today's betting lines plus free stats, injury and betting-percentage
data into one local database, then flags props that look mispriced. NFL first; other sports later.

It is a decision aid, not a "sure thing" generator. No bet is guaranteed. Bet only what you can afford to lose.

## What one run does

`.venv/bin/python -m betbot.main` (NFL by default):

1. **Updates NFL stats** if games have been played since your last update (only the current season is re-downloaded).
2. **Gets each game's opening and current line and prices** (total, spread, moneyline) from ESPN and prints how
   far each one moved.
3. **Gets the injury report**: game status from ESPN, practice participation from the official NFL reports.
4. **Gets public betting %** (% of bets, and % of money where it's free) from Action Network.
5. **Saves new DraftKings player-prop splits** (% of tickets and % of handle on each Over/Under) from DraftKings
   Network's articles, before DraftKings takes them down.
6. **Gets the kickoff weather forecast** (temperature, wind, gusts, chance of rain) for this week's games.
7. **Gets game odds at every US book** (moneyline, spread, total) from The Odds API: one request, 3 credits.
8. **Gets today's player prop lines** from The Odds API for games starting in the next 24 hours. This is the only
   step that costs API credits.

Everything is saved to `betbot.db`, so later runs only fetch what's new.

Then it builds the board: every prop and game pick with our probability, the book's price, the betting splits
and line movement, and a verdict (BET / FADE / STAY AWAY). It logs each pick, grades finished ones, and suggests
parlays. Pieces can also be run on their own (see Commands).

> **Backtest result: no proven edge.** Game lines 1999-2026 hit 51.6% (spreads) / 50.2% (totals) against the
> closing line, where 52.4% breaks even at -110, with coin-flip accuracy. Player props on 6 weeks of real
> DraftKings lines hit 52.6% after recalibration, i.e. break-even. Treat every pick as research, not a likely winner.

## The conviction score (what a run prints by default)

A run now prints the **best parlay** (`PARLAY_LEGS` legs, default 3, for a `PARLAY_STAKE` bet, default $100) with its
payout, chance of hitting and average return, even when that return is negative. The 95+ picks below are still scored
and logged for grading; run `.venv/bin/python -m betbot.nfl.conviction` to see them. The 95 bar itself is never lowered.
Game lines also weigh matchups, injuries (QB, defensive starters), rest, weather, prime-time hype, % of bets and money,
and line movement (`betbot/nfl/game_factors.py`, `steam.py`); every factor is fit on past games and tested on later ones.

The score is **not a probability.** It measures how many independent signals agree on Over or Under, and how strongly.
Each signal is rated from -1 to +1 toward the Over:

| Signal | What it looks at |
|---|---|
| script | an underdog, a team that has been losing, a team that throws a lot: more passing, less rushing |
| volume | his last 3 games vs. the line |
| stale line | the line still sits at last season's average while his recent games say otherwise |
| matchup | what the opposing defense allows to his position |
| usage | snap-share trend, starter or backup |
| health | questionable, limited in practice, his QB out, opposing starters out |
| weather | wind and cold |
| model | the player model's own probability |

Agreement minus twice the disagreement becomes a 0-100 score by its **percentile** among about 320,000 past props
(2019-2025), so 95 means "more agreement than 95% of all props ever scored". Market signals (sharp money against
us, a line that moved against us, a prediction market that disagrees) can only pull a score down, by up to 15.

A pick is shown only if all of these hold, and prints player, prop, line, side, book, price, score, historical hit
rate with its sample size, the book's no-vig %, EV per $100 and a one-line reason:
1. score 95 or higher;
2. at least 30 props in the 95+ range on **real DraftKings lines** (this season), so the hit rate is measured, not assumed;
3. that hit rate beats the book's no-vig price (positive EV).

Parlays use the measured hit rates (not the scores) with same-game correlation, and are sorted by EV.
Rebuild the tables with `.venv/bin/python -m betbot.nfl.conviction --build` (done automatically when a new week arrives).

**Honest status:** the stand-in lines used for the 2019-2025 history are slow averages, softer than a real book's, so
that history overstates hit rates (95+ hits 78% there). On real lines the score only separates at the very top
(scores of 90+ hit 76% on 21 props; everything below about 90 hit 50-55%), and only 7 props have reached 95 so far.

## How it decides

1. **Player model** (`nfl/model.py`): a recency-weighted average of the player's earlier games, adjusted for the
   opponent's defense vs. his position, team implied points, spread (game script), wind, cold, home, usage trend,
   injury report and practice status, starting QB out, and opposing defensive starters out. The size of each
   adjustment is fitted on past seasons. The spread of past errors turns the expectation into P(Over).
2. **Game model** (`nfl/game_model.py`): team ratings from points scored and allowed in earlier games, plus
   home-field edge, giving P(cover), P(over) and P(win).
3. **Recalibration**: both models' probabilities are corrected by how earlier predictions actually hit
   (walk-forward), and props again by how they did against real sportsbook lines. This pulls overconfident
   numbers toward 50%.
4. **Decision** (`decision.py`): no clear edge (EV under +2% or under 3 points over the book's no-vig
   probability) -> STAY AWAY; sharp money against us, a bait line, steam against us, or Kalshi pricing our side
   3+ points below the book -> STAY AWAY; fading a heavy
   public with sharp money on our side -> FADE; otherwise BET.
5. **Parlays** (`parlay.py`, `nfl/parlays.py`): only positive-EV legs; same-game legs use correlations measured
   from 2013-2025 results (e.g. QB passing yards vs. the game total: +0.28); shows the true combined probability,
   the probability if legs were independent, the price and EV.
6. **Grading** (`nfl/grader.py`): after each game, every logged pick gets its result and closing line value.

## What the app compares

For every prop or game, these are the pieces of data side by side. "Have" = collected and stored in `betbot.db`.

### The bet itself
| What | Example | Source | Status |
|---|---|---|---|
| Player prop line and price at each book | Josh Allen O/U 239.5 pass yds, -115 / -105 | The Odds API (6 US books) | Have (this week, saved every run) |
| Game spread, total, moneyline price at every US book | Packers -1.5 (-110) FanDuel, ML -118 | The Odds API (1 request, 3 credits per run) | Built; fills in from the next run |
| Game spread, total, moneyline: opening, current, closing, with prices | Packers -1.5 (-110), total 53.5, ML -115 / -105 | ESPN odds feed | Have (2024 on, plus every upcoming game) |
| Closing spread, total, moneyline for older games | Ravens -10.5, total 33.5 | nflverse | Have (1999 on; moneylines from about 2006) |
| How far the line moved since it opened | Total 45.5 -> 38.5 (-7) | ESPN open vs. current | Have |

### Who is betting what
| What | Example | Source | Status |
|---|---|---|---|
| Game % of bets (tickets) and % of money (handle) | Jaguars ML: 77% of bets, 65% of money | Action Network public page | Have (bets every game; money only where free), saved every run |
| Prediction-market probability for the same bet | Stafford Over 279.5: Kalshi 51.7% vs. book 50.0% | Kalshi | Have (from Oct 18, 2026; history builds each run) |
| Player-prop % of tickets and % of handle, Over vs. Under | Bo Nix pass yds 229.5: 24% of tickets / 22% of handle on the Over | DraftKings Network articles | Have (September 2026 on) |
| Past game % of bets and money | Chiefs -4.5: 65% of bets, 68% of handle | Internet Archive copies of Action Network (2018 on), VSiN (2023 on) and DraftKings Network (2024 on) | Have (about 300 scattered days; money % only where the page showed it free) |

### The player
| What | Example | Source | Status |
|---|---|---|---|
| Last 20 games of the stat, average and median | 246 avg / 246.5 median pass yds | nflverse weekly stats | Have (1999 on) |
| Role: snap %, targets, target share, carries | 85% of snaps, 24 targets, 44% target share | nflverse stats + snap counts, joined by player ID | Have (snaps 2013 on) |
| Injury: game status and practice participation | Questionable; limited in practice (knee) | ESPN injuries + official NFL practice reports | Have (current week) |

### Teammates and opponent
| What | Example | Source | Status |
|---|---|---|---|
| Key teammates out | His WR1 is Out | ESPN injuries + snap counts (starter = 60%+ of snaps) | Have |
| Opponent defensive starters out | Their top CB is Doubtful | Same | Have |
| What the opponent allows to his position | Lions allow 320 pass yds per game to QBs | nflverse stats (view `nfl_defense_vs_position_season`) | Have |

### The game setting
| What | Example | Source | Status |
|---|---|---|---|
| Expected points per team (game script) | Packers 27.5, Cowboys 26 | Total and spread (view `nfl_game_context`) | Have |
| Kickoff weather forecast | 52F, wind 10 mph, gusts 19, 0% rain | ESPN venue + Open-Meteo | Have (this week) |
| Past game weather, roof, surface | 40F, 12 mph, outdoors, grass | nflverse | Have (1999 on) |
| Rest days, divisional game, referee, coaches | 10 days rest vs. 7; referee Shawn Smith | nflverse | Have |

### Prices we don't have yet
| Missing | Why | How to get it |
|---|---|---|
| Prices on DraftKings prop splits | The articles only show the line and the % splits | Match each prop to the same player/line in the Odds API data (works from Oct 2026, when we started saving prop prices) |
| Player prop prices before Oct 17, 2026 | The free Odds API plan has no history; we only have what we've saved since | Paid Odds API history plan, or keep saving every run |
| Prices for prop markets other than QB passing yards | We only pull `player_pass_yds` | Add markets to the Odds API pull (about 12 credits each per run) |
| First-half / first-quarter prop prices | Not pulled | Odds API period markets (extra credits) |
| Sharp-book prices (Pinnacle, Circa) | Not in the Odds API's US books | Odds API non-US region (doubles the credits) |
| Moneylines for 1999-2005, and some 2006/2008 games | nflverse doesn't have them | No free source found |
| Price movement during the day | ESPN gives open / current / close only | More runs on game day, or a paid odds-history plan |

### Future data to add
- **Prices on DraftKings prop splits**: the articles don't list odds; match each prop to the same line in the Odds API data instead.
- **Archived consensus picks (2012 on)**: Covers and OddsShark pages saved by the Internet Archive show % of their users' picks (not real bets or money). Weaker signal; import labeled separately if wanted.
- **More player-prop markets from the Odds API**: rushing yards, receiving yards, receptions, TDs (about 12 credits each per run).
- **Sharp-book lines (Pinnacle, Circa)**: for "which book moved first"; needs the Odds API's non-US region (double the credits).
- **Line history within a day (steam)**: more runs on game day, or a paid odds-history plan.
- **Play-by-play detail**: red-zone touches, air yards per target, pass rate over expected (nflverse play-by-play, free).
- **Offensive line and pass-rush strength**: pressure rate allowed / generated (nflverse play-by-play or Next Gen Stats).
- **Travel and time zones**: distance and body-clock effect for West-to-East early games (stadium locations + schedule).
- **Coaching tendencies**: pace and pass rate by coach (play-by-play).
- **Other sports**: MLB (MLB Stats API, free), NBA, NHL, NCAAF, NCAAB, each in its own folder.

## Data sources

| Data | Source | Cost | How |
|---|---|---|---|
| Player prop lines (6 US books) | [The Odds API](https://the-odds-api.com) | Free tier: 500 credits/month | Official API, needs a key |
| NFL games since 1999 (scores, closing lines), weekly player stats, snap counts, practice reports | [nflverse](https://github.com/nflverse) | Free | Downloads public CSV files |
| Opening / current / closing game lines and prices (total, spread, moneyline) | ESPN | Free | ESPN's public odds feed (unofficial, may change) |
| Kickoff weather forecast | ESPN (stadium) + [Open-Meteo](https://open-meteo.com) | Free, no key | Stadium city -> coordinates -> hourly forecast at kickoff |
| Player ID links (connects snap counts to stats) | nflverse | Free | Downloads the public player table |
| Injury game status | ESPN | Free | ESPN's public injury feed (unofficial) |
| Player-prop % of tickets and % of handle (DraftKings), September 2026 on | [DraftKings Network](https://dknetwork.draftkings.com) articles | Free | Finds new articles through the site's public post list and saves each one once (1 second apart). The only free source of prop splits; DraftKings started publishing them in 2026, so there is no older history |
| Past game % of bets and % of money/handle, 2018 on | Internet Archive copies of Action Network, VSiN and DraftKings Network | Free | Lists every saved copy, downloads each once (1.5 s apart), reads it with the reader for that site and era, and matches each row to its nflverse game. Same free-money-only rule as live |
| Prediction-market prices (trader-set, no margin) for game winners, spread and total ladders, and player-prop ladders | [Kalshi](https://kalshi.com) | Free | Official public market-data API, no account or key; only price changes are saved |
| % of bets and % of money | [Action Network](https://www.actionnetwork.com/nfl/public-betting) | Free | Reads the public page once per run. Only uses what a logged-out visitor sees: % of bets for every game, % of money only for the few games shown free. Game lines only; no free source has prop splits |

Lines shown are real, bettable lines (DraftKings', or the most common one across books), never averages.
Opening lines from ESPN exist from 2024 on. Older seasons only have closing lines.

## Setup

Needs **Python 3.12 or newer** (on a Mac: `brew install python`). Then, once per clone:

```
./setup.sh
```

It creates a project-only Python environment (`.venv`, ignored by git) with the exact library versions the tests
passed with (`requirements.txt`), and copies `.env.example` to `.env`. Put your Odds API key in `.env` after
`ODDS_API_KEY=` (no quotes). Never commit `.env`; it's already in `.gitignore`.

Run everything with the project's Python, `.venv/bin/python`, so other Python installs on the machine don't matter.
(Or run `source .venv/bin/activate` once per terminal, then plain `python` works.)

Optional settings in `.env`:

| Setting | Default | |
|---|---|---|
| `SPORT` | `americanfootball_nfl` | Any [Odds API sport key](https://the-odds-api.com/sports-odds-data/sports-apis.html) |
| `MARKET` | `player_pass_yds` | Odds API prop market |
| `DB_PATH` | `betbot.db` | Where the database lives |
| `ODDS_REFRESH_HOURS` | `2` | Paid Odds API requests made within this many hours are skipped, so re-running costs nothing |
| `NO_PAID_REQUESTS` | off | `1` = never spend Odds API credits this run (free data only) |
| `FORCE_ODDS_PULL` | off | `1` = pull even if pulled recently or already this week (still blocked by `NO_PAID_REQUESTS`) |
| `PARLAY_ONLY` | `1` | Short output (default): the best parlay and any errors. `0` also prints the full board and the average-return-ranked parlays |
| `PARLAY_LEGS` / `PARLAY_STAKE` | `3` / `100` | Legs and bet amount for the best parlay a run prints |

## Commands

```
.venv/bin/python -m betbot.main                         # full run (uses Odds API credits)
.venv/bin/python -m betbot.nfl.signals.regression       # show picks from saved data (free)
.venv/bin/python -m betbot.nfl.collectors.nflverse      # update NFL stats (free; main does this automatically)
.venv/bin/python -m betbot.nfl.collectors.nflverse 1999 # full reload of every season
.venv/bin/python -m betbot.nfl.collectors.espn 2024     # one-time backfill of past opening/closing lines
.venv/bin/python -m betbot.collectors.action_network    # show current betting % (free)
.venv/bin/python -m betbot.collectors.dk_network nfl --all  # save every DraftKings prop-split article still online (free)
.venv/bin/python -m betbot.nfl.prop_splits              # saved prop splits as a table, by season and week (free)
.venv/bin/python -m betbot.nfl.collectors.weather       # this week's kickoff forecasts (free)
.venv/bin/python -m betbot.nfl.collectors.kalshi        # Kalshi prediction-market prices (free)
.venv/bin/python -m betbot.nfl.collectors.archive_splits  # import past game splits from the Internet Archive (free; re-runs skip done copies)
.venv/bin/python -m betbot.nfl.board                    # this week's verdicts from saved data (free)
.venv/bin/python -m betbot.nfl.parlays                  # positive-EV parlays with same-game correlation (free)
.venv/bin/python -m betbot.nfl.conviction --build       # rebuild the conviction score tables and hit rates (free, ~40 s)
.venv/bin/python -m betbot.nfl.conviction --player "Tyler Shough" --weeks 1-5   # a player's score week by week (free)
.venv/bin/python -m betbot.nfl.parlays --hunt           # plus-money (+150 to +400) legs with a 12%+ edge only (free)
.venv/bin/python -m betbot.nfl.parlays --hunt --min-conv 0.15 --min-price 150 --max-price 400 --legs 3
.venv/bin/python -m betbot.nfl.grader                   # grade finished picks (free)
.venv/bin/python -m betbot.nfl.backtest                 # hit rate, calibration, Brier, CLV on past games (free)
.venv/bin/python -m pytest                              # tests (fake data, no internet, no credits)
```

## API credits

Each full run costs 3 credits for game odds plus about 1 credit per game starting in the next 24 hours for props (about 15-18 on a Sunday). Everything else
is free. Tests and the picks check never use credits. Re-running within 2 hours (`ODDS_REFRESH_HOURS`) skips the paid
requests, and only lines or prices that actually changed are saved.

## Project layout

```
betbot/
  main.py                      one full run
  db.py                        SQLite tables + save helpers
  odds.py                      implied probability, vig removal, EV
  decision.py                  BET / FADE / STAY AWAY
  parlay.py                    correlated parlay probability and EV
  predictions.py               logs every pick for grading
  names.py                     player-name matching across sources
  collectors/
    odds_api.py                prop lines (every sport)
    action_network.py          public betting % (every sport)
    dk_network.py              DraftKings player-prop splits (every sport)
  nfl/
    teams.py                   team names -> abbreviations
    prop_splits.py             prop splits as a pandas DataFrame, by week
    collectors/nflverse.py     stats, snap counts, practice reports
    collectors/espn.py         game lines and prices, injuries
    collectors/weather.py      kickoff forecasts
    collectors/kalshi.py       Kalshi prediction-market prices
    collectors/archive_splits.py  past game splits from Internet Archive copies
    views.py                   combined views (usage, defense vs. position, game context)
    model.py                   player stat model and P(Over)
    game_model.py              team ratings, P(cover / over / win)
    board.py                   this week's picks and verdicts
    conviction.py              conviction score, hit-rate tables, 95+ picks and their parlays
    parlays.py, correlation.py parlays and same-game correlations
    grader.py, backtest.py     grading and backtests
    signals/regression.py      "chasing yesterday" picks
    signals/injuries.py        injury flags
tests/                         mirrors betbot/
```

Other sports will get their own folder next to `nfl/`.

## Database (`betbot.db`)

| Table | What's in it |
|---|---|
| `events`, `lines`, `game_odds` | Odds API games, every prop line snapshot, and every game moneyline/spread/total price at each book (history builds up with each run) |
| `nfl_games` | Every NFL game since 1999 with scores and closing lines |
| `nfl_player_weeks` | Weekly stats for every player since 1999 |
| `nfl_snap_counts` | Snap counts for every player since 2013 (used to tell who starts) |
| `nfl_players` | Player IDs across sources (links snap counts to stats) |
| `nfl_weather`, `nfl_venues` | Kickoff forecasts for this week's games; geocoded stadium cities |
| `nfl_practice_reports` | This season's official injury and practice reports |
| `nfl_injuries` | Latest ESPN injury statuses |
| `nfl_game_lines` | Opening, current and closing total, spread and moneyline, with prices, per game (2024+) |
| `splits` | Public % of bets / money with DraftKings' line and price, one row per fetch |
| `archive_splits`, `archive_captures` | Past game % of bets / money from archived pages (matched to nflverse games), and which saved copies were imported |
| `kalshi_markets` | Kalshi prices (bid / ask = probability) for NFL games and props, saved when they change |
| `predictions` | Every pick when made (side, line, price, our probability, book's no-vig probability), then its result and closing line value |
| `model_params`, `parlay_correlations` | Fitted model coefficients and measured same-game correlations |
| `nfl_injury_reports` | Official injury and practice reports since 2009 (the model learns from them) |
| `dk_articles`, `prop_splits` | DraftKings prop-split articles saved, and each prop's line, % of tickets and % of handle |

Ready-made views combine these: `nfl_player_usage` (snap %, targets, target share per game),
`nfl_defense_vs_position` and `nfl_defense_vs_position_season` (what each defense allows to QBs, RBs, WRs, TEs), and
`nfl_game_context` (rest, referee, roof, weather, lines, prices and each team's implied points).

The database lives only on your machine, in the project folder; it is not in git (it's large). Most of it can be
rebuilt by running the collectors again, but not the saved Odds API lines, betting % history or DraftKings prop
splits, because those sources don't keep old data. Back the file up somewhere safe.

## Known limits

- **No proven edge yet** (see the backtest above). Decision thresholds are reasonable defaults, not backtested.
- Prop backtests cover only 6 weeks of real lines, with -110 assumed (the DraftKings articles show no prices).
  Historical prop lines with prices cost money (The Odds API ~$59 for a month covers 2023 on; EndzoneAPI ~$19 a season).
- Same-game parlays: books price them with their own correlation adjustments, usually paying less than the
  multiplied odds shown here.

- ESPN's feeds and Action Network's page are unofficial and can break without notice; the app errors instead of saving
  bad data.
- The regression rule compares the last game to today's line until a week of line history has been saved.
- The injury report only shows each player's last practice status for the week.

## Future Ideas
- Create a FE to click a button and generate parlay for me given selections of mine, like number of legs, min odds, which game(s), etc. Then save this to compare with actual results at the end.
  - Would need APIs too for this
- Actual results update one game is completed.
- Tracking tab/page shows historic picks and results with a chart to see how many picks are correct v incorrect.