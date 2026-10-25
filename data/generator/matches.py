"""The three synthetic matches: who plays, the seed, and the scripted story.

Scene kinds (times are the match clock "MM:SS"; the second half starts at 45:00):
- dull    : `team` keeps the ball in midfield for at least `secs` seconds (no shots, no turnovers)
- goal    : `team` scores; `how` is buildup | counter | cross | corner | penalty | free_kick
- chance  : like goal, but the shot ends in `outcome` (Saved, Blocked, Off T, Post)
- red     : the next foul by `team` (by `player` if still on the pitch) is a straight red card
A scene fires at the first possession of `team` that starts at or after `at`.
"""

COMPETITION = {
    "competition_id": 9001, "season_id": 1, "country_name": "England",
    "competition_name": "Quick-Play Synthetic League", "competition_gender": "male",
    "competition_youth": False, "competition_international": False, "season_name": "2026/2027",
}

REFEREES = [
    {"id": 9401, "name": "Alan Pickering"},
    {"id": 9402, "name": "Sofia Marchetti"},
    {"id": 9403, "name": "Gavin Thorne"},
]

MATCHES = [
    {
        # Main demo match. Mirrors the worked example in docs/pipeline.md (Mensah vs Ward at ~34').
        "match_id": 9000001, "slug": "nbr-cal-seed42", "seed": 42,
        "home": "NBR", "away": "CAL", "date": "2026-08-15", "kick_off": "15:00:00.000",
        "match_week": 1, "referee": 0, "stoppage": [2, 4], "subs": [3, 4],
        "character": "Balanced 2-1 with long midfield spells, a big save by the Calder keeper, goals from three different routes.",
        "full_match": ["Mensah", "Okafor", "Ward", "Doyle"],
        "scenes": [
            {"at": "06:30", "team": "home", "kind": "dull", "secs": 40},
            {"at": "12:00", "team": "away", "kind": "dull", "secs": 35},
            {"at": "17:30", "team": "home", "kind": "dull", "secs": 55},
            {"at": "23:10", "team": "home", "kind": "goal", "how": "buildup", "scorer": "Mensah"},
            {"at": "28:00", "team": "away", "kind": "dull", "secs": 45},
            {"at": "33:40", "team": "home", "kind": "dull", "secs": 35},
            {"at": "34:30", "team": "home", "kind": "chance", "how": "buildup", "shooter": "Mensah", "outcome": "Saved"},
            {"at": "40:30", "team": "home", "kind": "dull", "secs": 95},
            {"at": "49:00", "team": "away", "kind": "dull", "secs": 40},
            {"at": "53:30", "team": "home", "kind": "dull", "secs": 50},
            {"at": "58:20", "team": "away", "kind": "goal", "how": "counter", "scorer": "Doyle"},
            {"at": "63:00", "team": "home", "kind": "dull", "secs": 60},
            {"at": "69:00", "team": "away", "kind": "dull", "secs": 35},
            {"at": "72:30", "team": "away", "kind": "chance", "how": "cross", "shooter": "Doyle", "outcome": "Off T"},
            {"at": "77:10", "team": "home", "kind": "goal", "how": "corner", "scorer": "Hale"},
            {"at": "82:00", "team": "home", "kind": "dull", "secs": 80},
            {"at": "88:30", "team": "away", "kind": "chance", "how": "buildup", "shooter": "Doyle", "outcome": "Blocked"},
        ],
    },
    {
        # Slow, possession-heavy match: tests the dull-moment detector and question cadence.
        "match_id": 9000002, "slug": "har-wex-seed7", "seed": 7,
        "home": "HAR", "away": "WEX", "date": "2026-08-16", "kick_off": "13:30:00.000",
        "match_week": 1, "referee": 1, "stoppage": [1, 3], "subs": [3, 3],
        "character": "Wexmoor pass it around for most of the match, Harrowgate sit deep and win 1-0 on the break.",
        "full_match": ["Sandberg", "Vos", "Arrieta"],
        "scenes": [
            {"at": "04:00", "team": "away", "kind": "dull", "secs": 60},
            {"at": "09:30", "team": "away", "kind": "dull", "secs": 75},
            {"at": "15:00", "team": "away", "kind": "dull", "secs": 50},
            {"at": "19:00", "team": "home", "kind": "dull", "secs": 30},
            {"at": "23:00", "team": "away", "kind": "dull", "secs": 90},
            {"at": "29:00", "team": "away", "kind": "chance", "how": "buildup", "shooter": "Vitale", "outcome": "Saved"},
            {"at": "32:00", "team": "away", "kind": "dull", "secs": 65},
            {"at": "38:00", "team": "away", "kind": "dull", "secs": 45},
            {"at": "42:00", "team": "home", "kind": "dull", "secs": 35},
            {"at": "47:30", "team": "away", "kind": "dull", "secs": 80},
            {"at": "53:00", "team": "away", "kind": "dull", "secs": 55},
            {"at": "58:00", "team": "away", "kind": "dull", "secs": 70},
            {"at": "63:00", "team": "home", "kind": "dull", "secs": 30},
            {"at": "67:10", "team": "home", "kind": "goal", "how": "counter", "scorer": "Sandberg"},
            {"at": "72:00", "team": "away", "kind": "dull", "secs": 60},
            {"at": "77:00", "team": "away", "kind": "dull", "secs": 85},
            {"at": "83:00", "team": "away", "kind": "chance", "how": "cross", "shooter": "Gallagher", "outcome": "Post"},
            {"at": "86:00", "team": "home", "kind": "dull", "secs": 45},
            {"at": "11:30", "team": "home", "kind": "chance", "how": "counter", "shooter": "Carver", "outcome": "Off T"},
            {"at": "44:00", "team": "away", "kind": "chance", "how": "buildup", "shooter": "Gallagher", "outcome": "Blocked"},
            {"at": "61:00", "team": "away", "kind": "chance", "how": "corner", "shooter": "Lemaire", "outcome": "Saved"},
            {"at": "75:00", "team": "home", "kind": "chance", "how": "counter", "shooter": "Sandberg", "outcome": "Saved"},
        ],
    },
    {
        # Chaotic 3-3: rare events (penalty goal, saved penalty, straight red, direct free kick).
        "match_id": 9000003, "slug": "wex-cal-seed2026", "seed": 2026,
        "home": "WEX", "away": "CAL", "date": "2026-08-22", "kick_off": "17:30:00.000",
        "match_week": 2, "referee": 2, "stoppage": [3, 6], "subs": [4, 4],
        "character": "End-to-end 3-3 with a penalty scored, a penalty saved, a red card and a free-kick goal.",
        "full_match": ["Vitale", "Doyle", "Ward", "Vos", "Arrieta", "Reid"],
        "scenes": [
            {"at": "08:40", "team": "home", "kind": "goal", "how": "buildup", "scorer": "Vitale"},
            {"at": "14:00", "team": "away", "kind": "dull", "secs": 35},
            {"at": "20:50", "team": "away", "kind": "goal", "how": "counter", "scorer": "Doyle"},
            {"at": "27:00", "team": "home", "kind": "dull", "secs": 45},
            {"at": "37:30", "team": "away", "kind": "goal", "how": "penalty", "scorer": "Ortega"},
            {"at": "41:00", "team": "home", "kind": "dull", "secs": 30},
            {"at": "50:00", "team": "home", "kind": "dull", "secs": 40},
            {"at": "54:40", "team": "home", "kind": "goal", "how": "free_kick", "scorer": "Arrieta"},
            {"at": "62:30", "team": "away", "kind": "red", "player": "Reid"},
            {"at": "66:00", "team": "away", "kind": "dull", "secs": 30},
            {"at": "70:40", "team": "away", "kind": "goal", "how": "cross", "scorer": "Doyle"},
            {"at": "75:00", "team": "home", "kind": "dull", "secs": 35},
            {"at": "79:30", "team": "home", "kind": "chance", "how": "penalty", "shooter": "Vitale", "outcome": "Saved"},
            {"at": "89:20", "team": "home", "kind": "goal", "how": "corner", "scorer": "Lemaire"},
        ],
    },
]
