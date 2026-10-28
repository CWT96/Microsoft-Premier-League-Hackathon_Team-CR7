"""The other matches on the demo's "Pick your side" screen: shown with their squads, not playable.

Only Northbridge v Calder has match data (the synthetic match). Wexmoor v Harrowgate
and Ashford v Kingsmere come from the design prototype so the screen looks like a real
matchday: fans can browse them and their squads, and the run itself plays Northbridge v Calder.
Squads are (num, name, pos) as drawn in the prototype; ids are ``<CODE>-<num>``.
"""

from __future__ import annotations

_XI_BENCH = {
    "WEX": ("Wexmoor", 7,
            [(1, "Brennan", "GK"), (30, "Okoye", "DEF"), (4, "Lindahl", "DEF"),
             (33, "Varga", "DEF"), (6, "Pemberton", "DEF"), (38, "Faure", "MID"),
             (10, "Hollis", "MID"), (8, "Marchetti", "MID"), (7, "Ashworth", "MID"),
             (18, "Danso", "FWD"), (9, "Strandberg", "FWD")],
            [(22, "Oduya", "FWD"), (29, "Leclair", "FWD"), (14, "Rinaldi", "FWD"),
             (12, "Corrigan", "DEF")]),
    "HAR": ("Harrowgate", 9,
            [(1, "Moretti", "GK"), (27, "Quayle", "DEF"), (3, "Halvorsen", "DEF"),
             (6, "Ebanks", "DEF"), (24, "Novak", "DEF"), (5, "Thorne", "MID"),
             (47, "Callaghan", "MID"), (10, "Benali", "MID"), (11, "Ferreira", "FWD"),
             (9, "Magnusson", "FWD"), (42, "Asante", "FWD")],
            [(21, "Dufresne", "DEF"), (8, "Petrov", "MID"), (7, "Mbeki", "FWD"),
             (45, "Kurtz", "DEF")]),
    "ASH": ("Ashford", 27,
            [(1, "Hollingworth", "GK"), (2, "Abara", "DEF"), (12, "Lindau", "DEF"),
             (4, "Brookes", "DEF"), (3, "Fenwick", "DEF"), (7, "Moreau", "MID"),
             (41, "Gately", "MID"), (67, "Harlow", "MID"), (19, "Eklund", "FWD"),
             (27, "Varela", "FWD"), (9, "Osei", "FWD")],
            [(11, "Prentice", "FWD"), (23, "Dunmore", "FWD"), (28, "Achebe", "MID"),
             (33, "Rowntree", "DEF")]),
    "KGM": ("Kingsmere", 7,
            [(31, "Vlasak", "GK"), (23, "Rivas", "DEF"), (37, "Holm", "DEF"),
             (6, "Dekker", "DEF"), (13, "Uwakwe", "DEF"), (16, "Castell", "MID"),
             (8, "Galloway", "MID"), (7, "Sorensen", "MID"), (20, "Amankwah", "FWD"),
             (19, "Selby", "FWD"), (11, "Tellier", "FWD")],
            [(9, "Ribeiro", "FWD"), (10, "Maddox", "MID"), (15, "Berglund", "MID"),
             (3, "Robb", "DEF")]),
}

# id, home, away, status line, meta, score, playable, predicted squads, fans calling it in the app
MATCHES = [
    {"id": "m1", "home": "NBR", "away": "CAL", "status": "LIVE", "meta": "Matchday 9",
     "score": None, "playable": True, "predicted": False, "fans": 128406},
    {"id": "m2", "home": "WEX", "away": "HAR", "status": "LIVE 61'", "meta": "Matchday 9",
     "score": "2 – 2", "playable": False, "predicted": False, "fans": 141873},
    {"id": "m3", "home": "ASH", "away": "KGM", "status": "TODAY", "meta": "Kick-off 17:30",
     "score": "vs", "playable": False, "predicted": True, "fans": 36210},
]


def other_teams() -> dict:
    """{code: {name, popular, players}} of the display-only teams, same shape as the demo teams."""
    out = {}
    for code, (name, popular, xi, bench) in _XI_BENCH.items():
        players = [{"id": f"{code}-{num}", "name": nm, "num": num, "pos": pos, "starter": starter}
                   for rows, starter in ((xi, True), (bench, False)) for num, nm, pos in rows]
        out[code] = {"name": name, "popular": f"{code}-{popular}", "players": players}
    return out
