"""Odds math shared by every sport. Prices are American odds (-110, +150)."""


def implied_prob(price):
    """Break-even probability the price implies, vig included: -110 -> 0.524, +150 -> 0.4."""
    price = float(price)
    return -price / (-price + 100) if price < 0 else 100 / (price + 100)


def no_vig(price, other_price):
    """This side's probability with the book's margin removed, using the other side's price. Returns
    (probability, True) or, when the other side is unknown, (vig-included probability, False)."""
    p = implied_prob(price)
    if other_price is None:
        return p, False
    q = implied_prob(other_price)
    return p / (p + q), True


def decimal_odds(price):
    """Total return per $1 staked, stake included: -110 -> 1.909, +150 -> 2.5."""
    price = float(price)
    return 1 + (100 / -price if price < 0 else price / 100)


def expected_value(prob, price):
    """Expected profit per $1 staked if `prob` is the true chance of winning at this price."""
    return prob * decimal_odds(price) - 1


def american(decimal):
    """Decimal odds back to American, e.g. a parlay's combined price: 2.5 -> +150, 1.5 -> -200."""
    return round((decimal - 1) * 100) if decimal >= 2 else round(-100 / (decimal - 1))
