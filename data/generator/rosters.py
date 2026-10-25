"""Fictional clubs, squads and the position vocabulary (StatsBomb / Hudl ids).

Everything here is invented. Any resemblance to real clubs or people is accidental.
"""
from __future__ import annotations

# StatsBomb position id -> (name, group, in-possession home spot (x, y) on a 120x80 pitch,
# own goal at x=0, right flank at high y).
POSITIONS: dict[int, tuple[str, str, tuple[float, float]]] = {
    1: ("Goalkeeper", "GK", (8, 40)),
    2: ("Right Back", "DF", (40, 70)),
    3: ("Right Center Back", "DF", (26, 52)),
    4: ("Center Back", "DF", (24, 40)),
    5: ("Left Center Back", "DF", (26, 28)),
    6: ("Left Back", "DF", (40, 10)),
    7: ("Right Wing Back", "DF", (55, 72)),
    8: ("Left Wing Back", "DF", (55, 8)),
    9: ("Right Defensive Midfield", "MF", (45, 50)),
    10: ("Center Defensive Midfield", "MF", (45, 40)),
    11: ("Left Defensive Midfield", "MF", (45, 30)),
    12: ("Right Midfield", "MF", (68, 70)),
    13: ("Right Center Midfield", "MF", (58, 52)),
    14: ("Center Midfield", "MF", (58, 40)),
    15: ("Left Center Midfield", "MF", (58, 28)),
    16: ("Left Midfield", "MF", (68, 10)),
    17: ("Right Wing", "FW", (84, 68)),
    19: ("Center Attacking Midfield", "MF", (76, 40)),
    21: ("Left Wing", "FW", (84, 12)),
    22: ("Right Center Forward", "FW", (92, 47)),
    23: ("Center Forward", "FW", (95, 40)),
    24: ("Left Center Forward", "FW", (92, 33)),
}

FORMATIONS: dict[int, list[int]] = {
    433: [1, 2, 3, 5, 6, 13, 10, 15, 17, 23, 21],
    4231: [1, 2, 3, 5, 6, 9, 11, 17, 19, 21, 23],
    442: [1, 2, 3, 5, 6, 12, 13, 15, 16, 22, 24],
    352: [1, 3, 4, 5, 7, 13, 10, 15, 8, 22, 24],
}

COUNTRY = {"id": 68, "name": "England"}

# Each squad: 11 starters in formation order, then 7 substitutes (GK, DF, DF, MF, MF, FW, FW).
# Player tuple: (jersey, full name, nickname or None).
# Style knobs are read by the engine (see engine.TeamStyle).
TEAMS: list[dict] = [
    {
        "team_id": 9101, "name": "Northbridge FC", "code": "NBR", "formation": 433,
        "stadium": {"id": 9201, "name": "Riverside Park"},
        "manager": {"id": 9301, "name": "Colm Brennan", "nickname": None, "dob": "1978-03-14"},
        "style": {"pass_acc": 0.82, "progress": 0.09, "direct": 0.15, "counter": 0.25,
                  "press": 0.24, "foul": 1.0, "dribble": 1.0},
        "squad": [
            (1, "Tomas Lindqvist", None), (2, "Ryan Achterberg", None), (5, "Kofi Hale", None),
            (4, "Mateo Duarte", None), (3, "Liam Fairbanks", None), (8, "Chidi Okafor", None),
            (6, "Jonah Pryce", None), (10, "Aurelien Sabatier", None), (7, "Dario Quintero", None),
            (9, "Samuel Mensah", None), (11, "Elliot Rook", None),
            (13, "Ben Ashdown", None), (15, "Isak Brandt", None), (22, "Noah Kettering", None),
            (14, "Felix Amadi", None), (18, "Luca Ferreri", None), (19, "Dev Raman", None),
            (20, "Owen Thistle", None),
        ],
    },
    {
        "team_id": 9102, "name": "Calder Town", "code": "CAL", "formation": 4231,
        "stadium": {"id": 9202, "name": "Calder Lane"},
        "manager": {"id": 9302, "name": "Ingrid Solheim", "nickname": None, "dob": "1981-11-02"},
        "style": {"pass_acc": 0.79, "progress": 0.13, "direct": 0.30, "counter": 0.45,
                  "press": 0.30, "foul": 1.15, "dribble": 1.1},
        "squad": [
            (1, "Harvey Ward", None), (2, "Marcus Oyelaran", None), (4, "Callum Reid", None),
            (5, "Piotr Zielinski", None), (3, "Nathan Grigg", None), (6, "Yusuf Demir", None),
            (8, "Arlo Penhale", None), (7, "Jamal Whitcombe", None), (10, "Tobias Venn", None),
            (11, "Rafael Ortega", None), (9, "Conor Doyle", None),
            (13, "Sami Laine", None), (15, "Kai Morrow", None), (23, "Ethan Blackwood", None),
            (16, "Joel Mbeki", None), (17, "Aidan Cross", None), (14, "Theo Lindgren", None),
            (19, "Dominic Hart", None),
        ],
    },
    {
        "team_id": 9103, "name": "Harrowgate Athletic", "code": "HAR", "formation": 442,
        "stadium": {"id": 9203, "name": "The Old Kiln"},
        "manager": {"id": 9303, "name": "Desmond Achebe", "nickname": None, "dob": "1975-06-21"},
        "style": {"pass_acc": 0.74, "progress": 0.16, "direct": 0.40, "counter": 0.55,
                  "press": 0.20, "foul": 1.1, "dribble": 0.9},
        "squad": [
            (1, "Gareth Pollard", None), (2, "Ruben Asante", None), (5, "Olly Marsh", None),
            (6, "Henrik Dahl", None), (3, "Kieran Bligh", None), (7, "Jordan Ikeme", None),
            (4, "Matthias Kroll", None), (8, "Sean Duffy", None), (11, "Ali Rahimi", None),
            (9, "Viktor Sandberg", None), (10, "Leon Carver", None),
            (13, "Finn Halloran", None), (15, "Patrick Nwosu", None), (12, "George Lyle", None),
            (14, "Emre Kaya", None), (16, "Tyler Brook", None), (18, "Malik Osei", None),
            (19, "Ivo Petrov", None),
        ],
    },
    {
        "team_id": 9104, "name": "Wexmoor Rovers", "code": "WEX", "formation": 352,
        "stadium": {"id": 9204, "name": "Moorgate Ground"},
        "manager": {"id": 9304, "name": "Paulo Esteves", "nickname": None, "dob": "1983-01-30"},
        "style": {"pass_acc": 0.84, "progress": 0.06, "direct": 0.10, "counter": 0.20,
                  "press": 0.28, "foul": 0.9, "dribble": 1.0},
        "squad": [
            (1, "Anton Vos", None), (4, "Declan Murray", None), (5, "Hugo Lemaire", None),
            (6, "Kwame Boateng", None), (2, "Ashton Pike", None), (8, "Rory Calloway", None),
            (16, "Benedikt Holm", None), (10, "Mikel Arrieta", None), (3, "Jesse Okonkwo", None),
            (9, "Lorenzo Vitale", None), (11, "Cian Gallagher", None),
            (13, "Will Darnell", None), (15, "Ezra Stein", None), (21, "Marco Bellandi", None),
            (14, "Oscar Pennington", None), (17, "Nils Eriksen", None), (7, "Zane Holloway", None),
            (18, "Harry Quill", None),
        ],
    },
]



def team_by_code(code: str) -> dict:
    return next(t for t in TEAMS if t["code"] == code)


def player_id(team: dict, jersey: int) -> int:
    """Stable integer player id, Hudl-style."""
    return team["team_id"] * 100 + jersey
