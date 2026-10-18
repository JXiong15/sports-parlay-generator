# sports-parlay-generator (BetBot)

A personal sports-betting research tool. It pulls today's betting lines plus free stats, injury and betting-percentage
data into one local database, then flags props that look mispriced. NFL first; other sports later.

It is a decision aid, not a "sure thing" generator. No bet is guaranteed. Bet only what you can afford to lose.

## What one run does

`python3 -m betbot.main` (NFL by default):

1. **Updates NFL stats** if games have been played since your last update (only the current season is re-downloaded).
2. **Gets each game's opening and current line** (total and spread) from ESPN and prints how far each one moved.
3. **Gets the injury report**: game status from ESPN, practice participation from the official NFL reports.
4. **Gets public betting %** (% of bets, and % of money where it's free) from Action Network.
5. **Saves new DraftKings player-prop splits** (% of tickets and % of handle on each Over/Under) from DraftKings
   Network's articles, before DraftKings takes them down.
6. **Gets today's player prop lines** from The Odds API for games starting in the next 24 hours. This is the only
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

## Data sources

| Data | Source | Cost | How |
|---|---|---|---|
| Player prop lines (6 US books) | [The Odds API](https://the-odds-api.com) | Free tier: 500 credits/month | Official API, needs a key |
| NFL games since 1999 (scores, closing lines), weekly player stats, snap counts, practice reports | [nflverse](https://github.com/nflverse) | Free | Downloads public CSV files |
| Opening / current / closing game lines (total, spread) | ESPN | Free | ESPN's public odds feed (unofficial, may change) |
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
    collectors/espn.py         game lines, injuries
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
| `nfl_snap_counts` | This season's snap counts (used to tell who starts) |
| `nfl_practice_reports` | This season's official injury and practice reports |
| `nfl_injuries` | Latest ESPN injury statuses |
| `nfl_game_lines` | Opening, current and closing total and spread per game (2024+) |
| `splits` | Public % of bets / money, one row per fetch |
| `dk_articles`, `prop_splits` | DraftKings prop-split articles saved, and each prop's line, % of tickets and % of handle |

The database lives only on your machine, in the project folder; it is not in git (it's large). Most of it can be
rebuilt by running the collectors again, but not the saved Odds API lines, betting % history or DraftKings prop
splits, because those sources don't keep old data. Back the file up somewhere safe.

## Known limits

- ESPN's feeds and Action Network's page are unofficial and can break without notice; the app errors instead of saving
  bad data.
- The regression rule compares the last game to today's line until a week of line history has been saved.
- The injury report only shows each player's last practice status for the week.
