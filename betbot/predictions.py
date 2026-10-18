"""Logs every pick with the model's probability and the book's no-vig probability at the time, so it can be
graded later against the result and the closing line (betbot/nfl/grader.py)."""
from datetime import datetime, timezone

from betbot import db
from betbot.odds import no_vig


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def prop_pick(result):
    """The side the model favors for one prop result from regression.evaluate, with its prices; None if the
    prop has no model probability or no price."""
    p_over = result.get("p_over")
    if p_over is None or result.get("over_price") is None:
        return None
    over = p_over >= 0.5
    price, other = (result["over_price"], result["under_price"]) if over else (result["under_price"], result["over_price"])
    if price is None:
        return None
    fair, vig_removed = no_vig(price, other)
    return {"side": "Over" if over else "Under", "model_prob": p_over if over else 1 - p_over, "price": price,
            "other_price": other, "implied_prob": fair, "vig_removed": int(vig_removed), "p_over": p_over}


def log_props(conn, results, made_at=None):
    """One row per prop (the favored side). Re-running with an unchanged line adds nothing."""
    made_at = made_at or _now()
    rows = []
    for r in results:
        pick = prop_pick(r)
        if pick is None or r.get("game_id") is None:
            continue
        rows.append(dict(pick, kind="prop", made_at=made_at, game_id=r["game_id"], event_id=r.get("event_id"),
                         player=r["player"], player_id=r["player_id"], market=r["market"], line=r["line"],
                         book=r["book"], mu=r.get("mu")))
    with conn:
        return db.save_predictions(conn, rows)


def log_games(conn, picks, made_at=None):
    """picks: dicts with game_id, market (spread/total/moneyline), side (home/away/over/under), line, price,
    other_price, model_prob, book."""
    made_at = made_at or _now()
    rows = []
    for p in picks:
        fair, vig_removed = no_vig(p["price"], p.get("other_price"))
        # "" not NULL: NULLs never count as duplicates, so re-runs would re-log the same game pick
        rows.append(dict(p, kind="game", made_at=made_at, event_id=None, player=None, player_id="",
                         implied_prob=fair, vig_removed=int(vig_removed), p_over=None, mu=p.get("mu")))
    with conn:
        return db.save_predictions(conn, rows)
