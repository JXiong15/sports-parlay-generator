from betbot import db
from betbot.nfl.collectors.espn import parse_injuries
from betbot.nfl.collectors.nflverse import replace_table
from betbot.nfl.signals.injuries import flags, practice, starters

PRACTICE_COLS = ["season", "week", "gsis_id", "practice_status", "practice_primary_injury", "report_primary_injury"]
SNAP_COLS = ["game_id", "season", "week", "player", "position", "team", "offense_pct", "defense_pct"]


def snaps(team, player, pos, pcts, side="offense", season=2026):
    # pcts[0] is the team's most recent game
    return [[f"{team}{season}_{len(pcts) - i}", season, len(pcts) - i, player, pos, team,
             pct if side == "offense" else 0, pct if side == "defense" else 0] for i, pct in enumerate(pcts)]


def make_db(snap_rows, injured, practice_rows=()):
    conn = db.connect(":memory:")
    replace_table(conn, "nfl_snap_counts", SNAP_COLS, snap_rows)
    replace_table(conn, "nfl_practice_reports", PRACTICE_COLS, practice_rows)
    db.replace_injuries(conn, [{"team": t, "player": p, "name_key": p.lower().replace(".", ""), "position": None,
                                "status": s, "fetched_at": "x"} for t, p, s in injured])
    return conn


def test_starters_use_last_three_games_only():
    rows = (snaps("GB", "Starter WR", "WR", [0.9, 0.8, 0.7, 0.0]) + snaps("GB", "Backup WR", "WR", [0.2, 0.3, 0.1, 1.0])
            + snaps("GB", "Old Starter", "WR", [0.0, 0.0, 0.0, 1.0]))
    assert starters(make_db(rows, []), "GB", "offense_pct") == {"starter wr": "WR"}


def test_flags_player_teammate_and_opponent():
    rows = (snaps("GB", "Top WR", "WR", [0.9] * 3) + snaps("GB", "Backup WR", "WR", [0.2] * 3)
            + snaps("GB", "Jordan Love", "QB", [1.0] * 3)
            + snaps("CHI", "Shutdown CB", "CB", [0.95] * 3, side="defense")
            + snaps("CHI", "Their QB", "QB", [1.0] * 3))
    conn = make_db(rows, [("GB", "Jordan Love", "Questionable"), ("GB", "Top WR", "Out"), ("GB", "Backup WR", "Out"),
                          ("CHI", "Shutdown CB", "Doubtful"), ("CHI", "Their QB", "Out"), ("CHI", "Healthy Guy", "Active")])
    assert flags(conn, "Jordan Love", "love", "GB", "CHI", "passing_yards") == [
        "Jordan Love is Questionable",
        "teammate: WR Top WR (Out)",  # backup WR and their QB don't matter here
        "CHI missing: CB Shutdown CB (Doubtful)",
    ]
    assert flags(conn, "Jordan Love", "love", "GB", None, "passing_yards")[-1] == "teammate: WR Top WR (Out)"


def test_parse_injuries_maps_teams_and_skips_unknown():
    data = {"injuries": [
        {"displayName": "Green Bay Packers", "injuries": [
            {"status": "Out", "athlete": {"displayName": "Christian Watson", "position": {"abbreviation": "WR"}}},
            {"status": None, "athlete": {"displayName": "No Status"}}]},
        {"displayName": "Made Up Team", "injuries": [{"status": "Out", "athlete": {"displayName": "X"}}]},
    ]}
    assert parse_injuries(data, "t") == [{"team": "GB", "player": "Christian Watson", "name_key": "christian watson",
                                          "position": "WR", "status": "Out", "fetched_at": "t"}]


def test_practice_report_catches_healthy_but_hurt():
    conn = make_db([], [("CAR", "Bryce Young", "Active")], [
        [2026, 5, "young", "Did Not Participate In Practice", "Ankle", None],  # last week: ignored
        [2026, 6, "young", "Full Participation in Practice", "Knee", None],
        [2026, 6, "rester", "Did Not Participate In Practice", "Not injury related - resting player", None],
        [2026, 6, "gametime", None, None, "Hamstring"],
    ])
    assert flags(conn, "Bryce Young", "young", "CAR", None, "passing_yards") == [
        "Bryce Young practiced fully this week (Knee)"]
    assert practice(conn, "rester") is None and practice(conn, "nobody") is None
    assert practice(conn, "gametime") == (None, "Hamstring")


def test_starters_last_three_games_span_seasons():
    # last 3 team games: 2026 week 1, then 2025 weeks 18 and 17 -- not 2025's week 18 sorted above 2026's week 1
    row = lambda season, week, player, pct: [f"GB{season}_{week}", season, week, player, "WR", "GB", pct, 0]
    rows = [row(2026, 1, "New Starter", 0.9), row(2025, 18, "New Starter", 0.9), row(2025, 17, "New Starter", 0.9),
            row(2026, 1, "Gone", 0.0), row(2025, 18, "Gone", 0.0), row(2025, 17, "Gone", 0.0),
            row(2025, 16, "Gone", 0.95), row(2025, 15, "Gone", 0.95), row(2025, 14, "Gone", 0.95)]
    assert starters(make_db(rows, []), "GB", "offense_pct") == {"new starter": "WR"}
