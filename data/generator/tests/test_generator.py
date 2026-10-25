"""Run with: python -m pytest data/generator/tests  (from the repo root)."""
import hashlib
from pathlib import Path

from data.generator import check, generate
from data.generator.engine import xg

ROOT = Path(__file__).resolve().parents[3]


def _digest(d: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(d.rglob("*.json")):
        h.update(f.relative_to(d).as_posix().encode())
        h.update(f.read_bytes())
    return h.hexdigest()


def _generate(out: Path, monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["generate", "--out", str(out)])
    generate.main()


def test_output_is_deterministic(tmp_path, monkeypatch):
    _generate(tmp_path / "a", monkeypatch)
    _generate(tmp_path / "b", monkeypatch)
    assert _digest(tmp_path / "a") == _digest(tmp_path / "b")


def test_generated_data_passes_all_hard_checks(tmp_path, monkeypatch):
    _generate(tmp_path, monkeypatch)
    monkeypatch.setattr("sys.argv", ["check", "--dir", str(tmp_path)])
    assert check.main() == 0


def test_committed_data_matches_generator(tmp_path, monkeypatch):
    """data/source/ must be exactly what the generator produces (no hand edits)."""
    _generate(tmp_path, monkeypatch)
    committed = ROOT / "data" / "source"
    for f in sorted(tmp_path.rglob("*.json")):
        rel = f.relative_to(tmp_path)
        assert (committed / rel).read_bytes() == f.read_bytes(), f"{rel} differs; rerun the generator"


def test_xg_is_higher_close_to_goal():
    assert xg((114, 40)) > xg((102, 40)) > xg((90, 40))
    assert xg((108, 40), header=True) < xg((108, 40))
    assert 0 < xg((90, 10)) < 0.05


def test_engine_matches_real_player_rates_on_average():
    """Over 15 extra seeds, the per-90 rates the question bank prices from are within 20% of real."""
    import collections

    from data.generator.matches import MATCHES
    cnt, mins = collections.defaultdict(collections.Counter), collections.Counter()
    for seed in range(100, 105):
        for cfg in MATCHES:
            sim, _, _ = generate.simulate(dict(cfg, seed=seed * 7 + cfg["seed"]))
            evs, lus = generate.events(sim), generate.lineups(sim)
            c, m = check.contract_rates(check.contract_view(evs), lus, evs)
            for g in m:
                cnt[g].update(c[g])
                mins[g] += m[g]
    for g, ref in check.REFERENCE_CONTRACT.items():
        for stat, real in ref.items():
            v = cnt[g][stat] / mins[g] * 90
            assert 0.8 * real <= v <= 1.2 * real, f"{g} {stat} per 90 = {v:.2f}, real {real}"
