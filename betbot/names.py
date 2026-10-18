"""Matching names across sources (The Odds API, ESPN, nflverse)."""
import re


def normalize(name):
    name = re.sub(r"[.'\-]", "", name.lower())
    return " ".join(w for w in name.split() if w not in ("jr", "sr", "ii", "iii", "iv", "v"))
