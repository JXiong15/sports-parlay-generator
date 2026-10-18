# sports-parlay-generator (BetBot)

A personal sports-betting research tool. It pulls today's betting lines plus free stats, injury and betting-percentage
data into one local database, then flags props that look mispriced. NFL first; other sports later.

It is a decision aid, not a "sure thing" generator. No bet is guaranteed. Bet only what you can afford to lose.

## What one run does

`python3 -m betbot.main` (NFL by default):

1. **Updates NFL stats** if games have been played since your last update (only the current season is re-downloaded).
2. **Gets each game's opening and current line and prices** (total, spread, moneyline) from ESPN and prints how
   far each one moved.
3. **Gets the injury report**: game status from ESPN, practice participation from the official NFL reports.
4. **Gets public betting %** (% of bets, and % of money where it's free) from Action Network.
5. **Saves new DraftKings player-prop splits** (% of tickets and % of handle on each Over/Under) from DraftKings
   Network's articles, before DraftKings takes them down.
6. **Gets the kickoff weather forecast** (temperature, wind, gusts, chance of rain) for this week's games.
7. **Gets today's player prop lines** from The Odds API for games starting in the next 24 hours. This is the only
   step that costs API credits.

Everything is saved to `betbot.db`, so later runs only fetch what's new.

Then check the picks:

```
python3 -m betbot.nfl.signals.regression
```

```
UNDER Jordan Love: line 271.5 (last 367, 20-game mean 246.2 / median 246.5)
      ! Jordan Love practiced fully this week (Knee)
      ! CHI missing: CB Some Player (Out)
```

## Signals (what it looks for)

| Signal | Status | What it does |
|---|---|---|
| Regression ("chasing yesterday") | Built | Lean UNDER when a big last game pushed the line above the player's 20-game average and median |
| Injury context | Built (flags only) | Flags the player if he's hurt or limited in practice, his team's starters who are out, and the opponent's defensive starters who are out |
| Line movement, trap lines, sharp vs. public money, steam | Not yet | Data is collected (opening/current lines, betting %); the rules aren't written yet |
| Decision (BET / FADE / STAY AWAY) and EV | Not yet | |

Thresholds are untested guesses until they're backtested. Flags don't change any numbers yet.

## What the app compares

For every prop or game, these are the pieces of data side by side. "Have" = collected and stored in `betbot.db`.

### The bet itself
| What | Example | Source | Status |
|---|---|---|---|
| Player prop line and price at each book | Josh Allen O/U 239.5 pass yds, -115 / -105 | The Odds API (6 US books) | Have (this week, saved every run) |
| Game spread, total, moneyline: opening, current, closing, with prices | Packers -1.5 (-110), total 53.5, ML -115 / -105 | ESPN odds feed | Have (2024 on, plus every upcoming game) |
| Closing spread, total, moneyline for older games | Ravens -10.5, total 33.5 | nflverse | Have (1999 on; moneylines from about 2006) |
| How far the line moved since it opened | Total 45.5 -> 38.5 (-7) | ESPN open vs. current | Have |

### Who is betting what
| What | Example | Source | Status |
|---|---|---|---|
| Game % of bets (tickets) and % of money (handle) | Jaguars ML: 77% of bets, 65% of money | Action Network public page | Have (bets every game; money only where free), saved every run |
| Player-prop % of tickets and % of handle, Over vs. Under | Bo Nix pass yds 229.5: 24% of tickets / 22% of handle on the Over | DraftKings Network articles | Have (September 2026 on) |
| Past game % of bets and money | Chiefs -4.5: 65% of bets, 68% of handle | VSiN pages saved by the Internet Archive | Surveying (2023 on, scattered days) |

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
| Game prices from more than one book | ESPN gives one book (DraftKings or ESPN BET); Action Network's we save is DraftKings | Odds API game odds, all US books in one request (about 3 credits per run) |
| Sharp-book prices (Pinnacle, Circa) | Not in the Odds API's US books | Odds API non-US region (doubles the credits) |
| Moneylines for 1999-2005, and some 2006/2008 games | nflverse doesn't have them | No free source found |
| Price movement during the day | ESPN gives open / current / close only | More runs on game day, or a paid odds-history plan |

### Future data to add
- **Prices on DraftKings prop splits**: the articles don't list odds; match each prop to the same line in the Odds API data instead.
- **Archived game splits (2023 on)**: import the Internet Archive copies of VSiN's DraftKings splits page (survey in progress).
- **More player-prop markets from the Odds API**: rushing yards, receiving yards, receptions, TDs (about 12 credits each per run).
- **This week's game moneylines and prices from several books**: the Odds API's game odds (about 3 credits per run); ESPN only gives one book.
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
| % of bets and % of money | [Action Network](https://www.actionnetwork.com/nfl/public-betting) | Free | Reads the public page once per run. Only uses what a logged-out visitor sees: % of bets for every game, % of money only for the few games shown free. Game lines only; no free source has prop splits |

Lines shown are real, bettable lines (DraftKings', or the most common one across books), never averages.
Opening lines from ESPN exist from 2024 on. Older seasons only have closing lines.

## Setup

Needs Python 3 (3.11+ recommended; the code also runs on 3.9).

```
pip3 install -r requirements.txt
cp .env.example .env
```

Put your Odds API key in `.env` after `ODDS_API_KEY=` (no quotes). Never commit `.env`; it's already in `.gitignore`.

Optional settings in `.env`:

| Setting | Default | |
|---|---|---|
| `SPORT` | `americanfootball_nfl` | Any [Odds API sport key](https://the-odds-api.com/sports-odds-data/sports-apis.html) |
| `MARKET` | `player_pass_yds` | Odds API prop market |
| `DB_PATH` | `betbot.db` | Where the database lives |

## Commands

```
python3 -m betbot.main                         # full run (uses Odds API credits)
python3 -m betbot.nfl.signals.regression       # show picks from saved data (free)
python3 -m betbot.nfl.collectors.nflverse      # update NFL stats (free; main does this automatically)
python3 -m betbot.nfl.collectors.nflverse 1999 # full reload of every season
python3 -m betbot.nfl.collectors.espn 2024     # one-time backfill of past opening/closing lines
python3 -m betbot.collectors.action_network    # show current betting % (free)
python3 -m betbot.collectors.dk_network nfl --all  # save every DraftKings prop-split article still online (free)
python3 -m betbot.nfl.prop_splits              # saved prop splits as a table, by season and week (free)
python3 -m betbot.nfl.collectors.weather       # this week's kickoff forecasts (free)
python3 -m pytest                              # tests (fake data, no internet, no credits)
```

## API credits

Each full run costs about 1 credit per game starting in the next 24 hours (about 12-15 on a Sunday). Everything else
is free. Tests and the picks check never use credits.

## Project layout

```
betbot/
  main.py                      one full run
  db.py                        SQLite tables + save helpers
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
    views.py                   combined views (usage, defense vs. position, game context)
    signals/regression.py      "chasing yesterday" picks
    signals/injuries.py        injury flags
tests/                         mirrors betbot/
```

Other sports will get their own folder next to `nfl/`.

## Database (`betbot.db`)

| Table | What's in it |
|---|---|
| `events`, `lines` | Odds API games and every prop line snapshot (history builds up with each run) |
| `nfl_games` | Every NFL game since 1999 with scores and closing lines |
| `nfl_player_weeks` | Weekly stats for every player since 1999 |
| `nfl_snap_counts` | Snap counts for every player since 2013 (used to tell who starts) |
| `nfl_players` | Player IDs across sources (links snap counts to stats) |
| `nfl_weather`, `nfl_venues` | Kickoff forecasts for this week's games; geocoded stadium cities |
| `nfl_practice_reports` | This season's official injury and practice reports |
| `nfl_injuries` | Latest ESPN injury statuses |
| `nfl_game_lines` | Opening, current and closing total, spread and moneyline, with prices, per game (2024+) |
| `splits` | Public % of bets / money with DraftKings' line and price, one row per fetch |
| `dk_articles`, `prop_splits` | DraftKings prop-split articles saved, and each prop's line, % of tickets and % of handle |

Ready-made views combine these: `nfl_player_usage` (snap %, targets, target share per game),
`nfl_defense_vs_position` and `nfl_defense_vs_position_season` (what each defense allows to QBs, RBs, WRs, TEs), and
`nfl_game_context` (rest, referee, roof, weather, lines, prices and each team's implied points).

The database lives only on your machine, in the project folder; it is not in git (it's large). Most of it can be
rebuilt by running the collectors again, but not the saved Odds API lines, betting % history or DraftKings prop
splits, because those sources don't keep old data. Back the file up somewhere safe.

## Known limits

- ESPN's feeds and Action Network's page are unofficial and can break without notice; the app errors instead of saving
  bad data.
- The regression rule compares the last game to today's line until a week of line history has been saved.
- The injury report only shows each player's last practice status for the week.
