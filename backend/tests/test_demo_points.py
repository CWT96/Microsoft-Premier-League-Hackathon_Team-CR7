from backend.demo.points import score_call


def test_too_quick_keeps_streak():
    s = score_call(None, "PASS", 2)
    assert s.too_quick and s.points == 0 and s.streak == 2


def test_wrong_pays_ten_resets_streak():
    s = score_call("SHOOT", "PASS", 4)
    assert (s.correct, s.points, s.streak) == (False, 10, 0)


def test_correct_without_streak_bonus():
    s = score_call("PASS", "PASS", 1)
    assert (s.points, s.streak, s.streak_bonus) == (110, 2, 0)


def test_streak_bonus_steps():
    assert score_call("PASS", "PASS", 2).streak_bonus == 50   # 3rd in a row
    assert score_call("PASS", "PASS", 3).points == 210        # 4th: 10 + 100 + 100
