# sports-parlay-generator

Personal prop-line research tool. See `.claude/PLAN.md`.

## Setup
```
pip install -r requirements.txt
cp .env.example .env   # add your The Odds API key
python -m betbot.main  # pulls today's props into betbot.db
python -m pytest
```

## Credits
Each run costs 1 credit per game starting in the next 24h (free tier: 500/month).

## NFL history (free, no key)
```
python -m betbot.nfl.collectors.nflverse        # every game since 1999 + weekly player stats
python -m betbot.nfl.collectors.nflverse 2015   # or start from a later season
```
`python -m betbot.main` reloads this automatically when it is out of date.
