"""Demo relabel: Arsenal vs Chelsea names, shirt numbers and the fixed pitch frame.

The demo plays a synthetic match relabelled as Arsenal (home, NBR) vs Chelsea (away, CAL).
Internal team codes stay NBR / CAL; display codes are ARS / CHE.
"""

from __future__ import annotations

TEAMS: dict[str, dict[str, str]] = {
    "NBR": {"code": "ARS", "name": "Arsenal", "crest": "assets/ars.svg"},
    "CAL": {"code": "CHE", "name": "Chelsea", "crest": "assets/che.svg"},
}

# synthetic player id -> (design name, shirt number)
PLAYERS: dict[str, tuple[str, int]] = {
    "910101": ("Raya", 1),
    "910102": ("White", 4),
    "910105": ("Saliba", 2),
    "910104": ("Gabriel", 6),
    "910103": ("Calafiori", 33),
    "910108": ("Rice", 41),
    "910106": ("Zubimendi", 36),
    "910110": ("Ødegaard", 8),
    "910109": ("Saka", 7),
    "910107": ("Gyökeres", 14),
    "910111": ("Eze", 10),
    "910119": ("Havertz", 29),
    "910120": ("Timber", 12),
    "910115": ("Martinelli", 11),
    "910113": ("Merino", 23),
    "910122": ("Bruno Guimarães", 39),
    "910201": ("Sánchez", 1),
    "910202": ("James", 24),
    "910204": ("Fofana", 3),
    "910205": ("Colwill", 6),
    "910203": ("Hato", 21),
    "910206": ("Caicedo", 25),
    "910208": ("Enzo", 8),
    "910210": ("Palmer", 10),
    "910207": ("Neto", 7),
    "910209": ("João Pedro", 9),
    "910211": ("Estêvão", 41),
    "910215": ("Gusto", 27),
    "910214": ("Delap", 12),
    "910223": ("Lavia", 45),
    "910219": ("Gittens", 11),
}


def display_name(player_id: str | None) -> str:
    """Design name for a synthetic player id; unknown or None gives an empty string."""
    if player_id is None or player_id not in PLAYERS:
        return ""
    return PLAYERS[player_id][0]


def fixed_xy(
    team: str, xy: tuple[float, float] | None, home: str = "NBR"
) -> tuple[float, float] | None:
    """Pitch position in the fixed frame: home attacks left to right, away is rotated 180 degrees."""
    if xy is None:
        return None
    x, y = xy
    if team == home:
        return (round(x, 1), round(y, 1))
    return (round(105 - x, 1), round(68 - y, 1))


def clock_label(half: int, t: float) -> str:
    """Displayed match clock: ``MM:SS``, or ``45+M:SS`` / ``90+M:SS`` in stoppage time."""
    seconds = int(t)
    if half == 1 and seconds >= 2700:
        return f"45+{(seconds - 2700) // 60}:{(seconds - 2700) % 60:02d}"
    if half == 2 and seconds >= 5400:
        return f"90+{(seconds - 5400) // 60}:{(seconds - 5400) % 60:02d}"
    return f"{seconds // 60:02d}:{seconds % 60:02d}"
