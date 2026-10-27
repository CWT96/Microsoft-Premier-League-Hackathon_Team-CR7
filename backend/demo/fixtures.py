"""The other matches on the demo's "Pick your side" screen: shown with their squads, not playable.

Only Arsenal v Chelsea has match data (the synthetic match, relabelled). Liverpool v Man City
and Newcastle v Tottenham come from the design prototype so the screen looks like a real
matchday: fans can browse them and their squads, and the run itself plays Arsenal v Chelsea.
Squads are (num, name, pos) as drawn in the prototype; ids are ``<CODE>-<num>``.
"""

from __future__ import annotations

_XI_BENCH = {
    "LIV": ("Liverpool", 7,
            [(1, "Alisson", "GK"), (30, "Frimpong", "DEF"), (4, "van Dijk", "DEF"),
             (33, "Araújo", "DEF"), (6, "Kerkez", "DEF"), (38, "Gravenberch", "MID"),
             (10, "Mac Allister", "MID"), (8, "Szoboszlai", "MID"), (7, "Wirtz", "MID"),
             (18, "Gakpo", "FWD"), (9, "Isak", "FWD")],
            [(22, "Ekitike", "FWD"), (29, "Barcola", "FWD"), (14, "Chiesa", "FWD"),
             (12, "Bradley", "DEF")]),
    "MCI": ("Man City", 9,
            [(1, "Donnarumma", "GK"), (27, "Nunes", "DEF"), (3, "Dias", "DEF"),
             (6, "Guéhi", "DEF"), (24, "Gvardiol", "DEF"), (5, "Anderson", "MID"),
             (47, "Foden", "MID"), (10, "Cherki", "MID"), (11, "Doku", "FWD"),
             (9, "Haaland", "FWD"), (42, "Semenyo", "FWD")],
            [(21, "Aït-Nouri", "DEF"), (8, "Kovačić", "MID"), (7, "Ndiaye", "FWD"),
             (45, "Khusanov", "DEF")]),
    "NEW": ("Newcastle", 27,
            [(1, "Pope", "GK"), (2, "Livramento", "DEF"), (12, "Thiaw", "DEF"),
             (4, "Botman", "DEF"), (3, "Hall", "DEF"), (7, "Joelinton", "MID"),
             (41, "Ramsey", "MID"), (67, "Miley", "MID"), (19, "Elanga", "FWD"),
             (27, "Woltemade", "FWD"), (9, "Wissa", "FWD")],
            [(11, "Barnes", "FWD"), (23, "Murphy", "FWD"), (28, "Willock", "MID"),
             (33, "Burn", "DEF")]),
    "TOT": ("Tottenham", 7,
            [(31, "Kinsky", "GK"), (23, "Porro", "DEF"), (37, "van de Ven", "DEF"),
             (6, "van Hecke", "DEF"), (13, "Udogie", "DEF"), (16, "Tonali", "MID"),
             (8, "Gallagher", "MID"), (7, "Simons", "MID"), (20, "Kudus", "FWD"),
             (19, "Solanke", "FWD"), (11, "Tel", "FWD")],
            [(9, "Richarlison", "FWD"), (10, "Maddison", "MID"), (15, "Bergvall", "MID"),
             (3, "Robertson", "DEF")]),
}

# id, home, away, status line, meta, score, playable, predicted squads
MATCHES = [
    {"id": "m1", "home": "ARS", "away": "CHE", "status": "LIVE", "meta": "Matchweek 9",
     "score": None, "playable": True, "predicted": False},
    {"id": "m2", "home": "LIV", "away": "MCI", "status": "LIVE 61'", "meta": "Matchweek 9",
     "score": "2 – 2", "playable": False, "predicted": False},
    {"id": "m3", "home": "NEW", "away": "TOT", "status": "TODAY", "meta": "Kick-off 17:30",
     "score": "vs", "playable": False, "predicted": True},
]


def other_teams() -> dict:
    """{code: {name, popular, players}} of the display-only teams, same shape as the demo teams."""
    out = {}
    for code, (name, popular, xi, bench) in _XI_BENCH.items():
        players = [{"id": f"{code}-{num}", "name": nm, "num": num, "pos": pos, "starter": starter}
                   for rows, starter in ((xi, True), (bench, False)) for num, nm, pos in rows]
        out[code] = {"name": name, "popular": f"{code}-{popular}", "players": players}
    return out
