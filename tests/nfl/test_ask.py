from betbot.nfl.ask import flags


def test_trap_flags():
    # public 75% on the Over, line dropped (moved against the public), average inflated by one big game
    out = flags(0.52, 0.52, 0, -1.5, 75, 0.4, 12, 19, 17.5, "Active", 1)
    assert any("public side" in f for f in out) and any("moved against the public" in f for f in out)
    assert any("inflates" in f for f in out)
    assert flags(0.5, 0.5, 0, 0, 55, 0.5, 20, 20, 20, "Active", 1) == []
    assert any("still has a price" in f for f in flags(0.5, 0.5, 0, 0, None, None, None, 0, 0, "Out", 1))
