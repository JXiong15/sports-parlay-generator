# BetBot: instructions for any AI assistant (VS Code Copilot, Cursor, ChatGPT, etc.)
Personal NFL betting research tool. Read README.md and .claude/PLAN.md for the full plan. Use `.venv/bin/python -m ...`.
Say "avg return", not "EV". The backtest shows no proven edge: mention it once, briefly. Never commit keys (.env) or the picks/ folder.

Commands:
- `.venv/bin/python -m betbot.nfl.run_picks`  **run this every time the user asks for lines, picks or a parlay**, before answering, so the odds are current (skips the paid pull if saved odds are under 2h old; ~63 credits otherwise; quote that first if the key is active)
- `.venv/bin/python -m betbot.main`  full run (pulls fresh odds for games in the next 24h, uses Odds API credits: 3 for game lines + 1 per game per prop market)
- `NO_PAID_REQUESTS=1 .venv/bin/python -m betbot.main`  free data only
- `.venv/bin/python -m betbot.nfl.ask "Player Name" [market]`  trap check on one player's props
- `.venv/bin/python -m pytest`  tests
## Picks: the process (always, no exceptions; set Oct 26, 2026 after a 5/13 night)
When asked for picks, props, "today's games" or "what to bet", do ALL of this unprompted. The user should never have to tell me a step.
1. **Fresh data first.** Check the age of game odds, props, injuries, Kalshi, splits. Pull fresh odds for every game starting in the next 24h (game lines + every prop market; quote credits, then just do it) and all free sources. Never answer from stale data; say how old each source is.
2. **Predict the game first:** projected score, margin, pace, run/pass split, from both offenses AND defenses (EPA, pressure, pace), injuries by position group, line moves and why, weather, rest, travel, referee.
3. **Roles after injuries:** for every Out/Doubtful/Questionable player, say who gets his targets, carries or snaps. Don't trust last-5 averages across a role change. Use medians and hit rates, not just means.
4. **Every prop must agree with the game script.** A leading team runs more and passes less; a trailing team the reverse. Never list picks that contradict each other. Check teammate patterns (who pops when) from the data for every pair, but treat them as angles, not rules: sometimes both pop.
5. **Check every prop line on the board** (all players, all markets), not a handful. Include public vs. money splits, prop splits (tested on 979 archived games: the heavy-public side wins 49-52% of the time, so a public-heavy side alone is NOT a fade signal; weigh it only with money/ticket gaps or line moves against it), Kalshi, price differences between books, and traps (Out player still priced, stale line, inflated average).
6. **Output:** one list, sorted by confidence % highest to lowest, with odds, a one-line why and the main risk. Include some plus-odds picks. Short. Confidence is my own estimate, never copied from the market's implied probability. Say the backtest shows no proven edge once, briefly.
7. **Save and grade:** write picks to `picks/week-NN/YYYY-MM-DD.md` (git-ignored). After games, grade every pick from ESPN's box score (`site.api.espn.com/.../summary?event=<id>`) and record what went wrong, so later weeks adjust.

## Asking about a prop
When the user asks about a player's prop ("is Lamb over 79.5 a trap?"), run `.venv/bin/python -m betbot.nfl.ask "Player Name" [market]` first (add `OFFLINE`-style freshness check: pull fresh odds if the saved lines are old), then answer with the picks process above: game script, role changes, the tool's trap flags, and my own confidence %. Short.

## Lessons from graded picks (read before every pick list; each is n=1 until tested, so treat as angles, not rules)
- **Wk 8 CAR@GB, 2 of 7 bets hit.** Receiving Unders on a trailing or close offense that throws 40+ times lost: the top 2-3 targets soak up volume (Watson 113 yds, Golden 13 tgts, McMillan 11 tgts). Before any receiving Under, check target share concentration and expected pass attempts, not just median and "targets will split".
- A road dog "likely trailing" stayed within one score, so the RB kept 22 carries. Don't Under RB volume on a trailing guess; use the spread (+3.5 is a one-score game).
- RB2 Over carries behind a healthy RB1 lost (Lloyd 3 att, Jacobs 21). Fade RB2 volume unless RB1 is Out.
- OL injuries: the line with 4 out allowed 0 sacks; the line with 3 out allowed 6. Count OL absences by who replaces them, not by number.
- Passes that won: Under 47.5 (46), Love O1.5 TD, Love O0.5 INT. Keep grading Passes; they are the calibration check.
- **Wk 9 (caught by the user): Coleman Over 25.5 yds.** His 63/116/59-yd games came when DJ Moore was out or hurt; with Moore healthy he had 1 and 37. Before any prop, split the player's recent games by whether the teammate ahead of him played, and check whether that teammate is back (Active, even limited). Medians and means over 20 games blend both roles.
- **Wk 9 review (500 props, Nov 1 board):** 52% hit; the model's edge over the book did NOT predict results (gap 6+ pts hit 47-49%, under 2 pts 55%). Confidence now = book + 0.2 x edge (cap 3 pts). The parlay still weighs the model at 0.5 (parlays.SHRINK), so its avg return is overstated. Receiving Unders are dropped when a WR/TE teammate is Out/Doubtful/IR/Suspended (RBs are not flagged for receiving props, so check them by hand; NE's Kyle Williams, PHI's Cooper still need a manual check: the flag only covers key teammates). Rush-yds Unders (QBs mostly, 56%) and receptions Unders (60%) held; yardage Over/Under was a coin flip. Prefer picks with a repeatable hit rate (6 of 7 games) over model-only edges.
