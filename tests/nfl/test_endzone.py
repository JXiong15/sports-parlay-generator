from betbot import db
from betbot.nfl import conviction
from betbot.nfl.collectors import endzone
from betbot.nfl.collectors.nflverse import replace_table


class Resp:
    status_code = 200

    def __init__(self, data):
        self.data = data

    def json(self):
        return {"data": self.data}


class Session:
    def __init__(self, replies):
        self.replies, self.calls = replies, []

    def post(self, url, json, headers, timeout):
        self.calls.append(json)
        return Resp(self.replies[json["player_id"]])


GAME = dict(game_id="2025_03_KC_NYG", game_date="2025-09-21", line=270.5, open_line=268.5, over_odds=-115,
            under_odds=-105, books=1)


def conn_with_players():
    conn = db.connect(":memory:")
    replace_table(conn, "nfl_player_weeks", ["player_id", "player_display_name", "position", "season", "week", "attempts",
                                             "carries", "targets"],
                  [["a", "Ann QB", "QB", 2025, w, 30, 0, 0] for w in (1, 2, 3)]
                  + [["b", "Bo Backup", "QB", 2025, 1, 2, 0, 0], ["c", "Cy QB", "QB", 2024, 1, 30, 0, 0]] * 3)
    return conn


def test_plan_skips_backups_and_what_was_already_pulled():
    conn = conn_with_players()
    assert [t[0] for t in endzone.plan(conn, ["player_pass_yds"])] == ["a", "c"]
    endzone.save(conn, "a", "player_pass_yds", [], "t")                    # an empty answer counts as pulled
    assert [t[0] for t in endzone.plan(conn, ["player_pass_yds"])] == ["c"]
    assert endzone.plan(conn, ["player_pass_yds"], max_credits=0) == []
    assert endzone.plan(conn, ["player_pass_yds", "player_rush_yds"]) == [("c", "Cy QB", "player_pass_yds")]


def test_pull_saves_lines_once_and_the_scoring_uses_the_opening_line():
    conn = conn_with_players()
    session = Session({"a": {"player_name": "Ann QB", "games": [GAME]}, "c": {"player_name": "Cy QB", "games": []}})
    endzone.pull(conn, endzone.plan(conn, ["player_pass_yds"]), "key", session, sleep=lambda s: None)
    assert len(session.calls) == 2 and session.calls[0]["bookmaker"] == "draftkings"
    assert endzone.plan(conn, ["player_pass_yds"]) == []                   # never asked again
    out = conviction.saved_lines(conn)
    assert out[["season", "week", "player", "line"]].values.tolist() == [[2025, 3, "Ann QB", 268.5]]
