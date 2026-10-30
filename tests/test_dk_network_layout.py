from betbot.collectors import dk_network


def test_separate_player_and_market_columns():
    html = ("<table><tr><th>Player</th><th>Market</th><th>Line</th><th>Handle split</th><th>Bet split</th></tr>"
            "<tr><td>Jordan Love</td><td>Passing attempts</td><td>35.5</td><td>Over 20% / Under 80%</td>"
            "<td>Over 100% / Under —</td></tr></table>")
    rows, bad = dk_network.parse(html)
    assert bad == [] and len(rows) == 1
    r = rows[0]
    assert (r["player"], r["market"], r["line"]) == ("Jordan Love", "player_pass_attempts", 35.5)
    assert (r["over_handle"], r["under_handle"], r["over_bets"], r["under_bets"]) == (20.0, 80.0, 100.0, None)
