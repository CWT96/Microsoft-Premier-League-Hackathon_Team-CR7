"""Demo display: Northbridge vs Calder names, shirt numbers and the fixed pitch frame.

The demo plays the synthetic match as Northbridge (home, NBR) vs Calder (away, CAL).
Team codes are NBR / CAL, internally and on screen.
"""

from __future__ import annotations

TEAMS: dict[str, dict[str, str]] = {
    "NBR": {"code": "NBR", "name": "Northbridge", "crest": "assets/crests/nbr.svg"},
    "CAL": {"code": "CAL", "name": "Calder", "crest": "assets/crests/cal.svg"},
}

# synthetic player id -> (design name, shirt number)
PLAYERS: dict[str, tuple[str, int]] = {
    "910101": ("Lindqvist", 1),
    "910102": ("Achterberg", 4),
    "910105": ("Hale", 2),
    "910104": ("Duarte", 6),
    "910103": ("Fairbanks", 33),
    "910108": ("Okafor", 41),
    "910106": ("Pryce", 36),
    "910110": ("Sabatier", 8),
    "910109": ("Mensah", 7),
    "910107": ("Quintero", 14),
    "910111": ("Rook", 10),
    "910119": ("Raman", 29),
    "910120": ("Thistle", 12),
    "910115": ("Brandt", 11),
    "910113": ("Ashdown", 23),
    "910122": ("Kettering", 22),
    "910201": ("Ward", 1),
    "910202": ("Oyelaran", 24),
    "910204": ("Reid", 3),
    "910205": ("Zelek", 6),
    "910203": ("Grigg", 21),
    "910206": ("Demir", 25),
    "910208": ("Penhale", 8),
    "910210": ("Venn", 10),
    "910207": ("Whitcombe", 7),
    "910209": ("Doyle", 9),
    "910211": ("Ortega", 41),
    "910215": ("Morrow", 27),
    "910214": ("Lindgren", 12),
    "910223": ("Blackwood", 45),
    "910219": ("Hart", 11),
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


# Squads the fan can back players from: display code -> (internal code, popular id,
# [(player id, position, starter)]), XI first then bench, in design order.
SQUADS: dict[str, tuple[str, str, list[tuple[str, str, bool]]]] = {
    "NBR": ("NBR", "910109", [
        ("910101", "GK", True), ("910102", "DEF", True), ("910105", "DEF", True),
        ("910104", "DEF", True), ("910103", "DEF", True), ("910108", "MID", True),
        ("910106", "MID", True), ("910110", "MID", True), ("910109", "FWD", True),
        ("910107", "FWD", True), ("910111", "FWD", True),
        ("910122", "MID", False), ("910119", "FWD", False), ("910115", "FWD", False),
        ("910113", "MID", False), ("910120", "DEF", False)]),
    "CAL": ("CAL", "910210", [
        ("910201", "GK", True), ("910202", "DEF", True), ("910204", "DEF", True),
        ("910205", "DEF", True), ("910203", "DEF", True), ("910206", "MID", True),
        ("910208", "MID", True), ("910210", "MID", True), ("910207", "FWD", True),
        ("910209", "FWD", True), ("910211", "FWD", True),
        ("910214", "FWD", False), ("910219", "FWD", False), ("910223", "MID", False),
        ("910215", "DEF", False)]),
}
