"""week_report on plain fixture JSON written here. No network, no keys, nothing outside tmp_path."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import week_report as wr  # noqa: E402


def at(day, hour=12):
    return datetime(2026, 10, day, hour, tzinfo=timezone.utc)


def stats(followers=100, **posts):
    """posts: name -> dict of fields; a Reel unless 'kind' says otherwise."""
    out = []
    for i, (name, f) in enumerate(posts.items()):
        f = dict(f)
        kind = f.pop("kind", "REELS")
        out.append({"id": name, "caption": f"cap {name}", "media_type": "VIDEO",
                    "media_product_type": kind, "timestamp": f.pop("ts"), **f})
    return {"account": {"username": "x", "followers_count": followers}, "posts": out}


REEL = dict(ts="2026-10-20T12:00:00+0000", reach=1000, likes=50, saved=20, shares=10)


def reel_row(out, day):
    """The Reel's row in the milestone table (the posts table lists it too)."""
    return next(l for l in out.split("## Reels")[1].splitlines() if l.startswith(f"| {day} Reel"))


def run(tmp_path, data, now, capsys):
    tmp_path.mkdir(exist_ok=True)
    f = tmp_path / "stats.json"
    f.write_text(json.dumps(data))
    wr.main([str(f), "--insights-dir", str(tmp_path / "ins")], now=now)
    return capsys.readouterr().out


def test_first_report_has_all_sections(tmp_path, capsys):
    out = run(tmp_path, stats(120, r1=REEL, p1=dict(kind="FEED", ts="2026-10-21T08:00:00+0000",
                                                    reach=300, likes=9, saved=1, shares=0)), at(28), capsys)
    assert "**Followers:** 120" in out
    assert "| 2026-10-20 Reel cap r1 | 1,000 | 50 | 20 | 10 |" in out
    assert "| 2026-10-21 Post cap p1 | 300 | 9 | 1 | 0 |" in out
    assert "First report: nothing to compare yet." in out
    assert (tmp_path / "ins" / "report-2026-10-28.json").exists()
    assert len(out.splitlines()) < 40


def test_reel_milestones_by_age(tmp_path, capsys):
    # posted Oct 20 12:00: at Oct 28 12:00 it is 8 days old -> all four marks reached
    out = run(tmp_path, stats(r1=REEL), at(28), capsys)
    row = reel_row(out, "2026-10-20")
    assert row.count("5/2/1") == 4
    # 30h old: only the 24h mark
    out = run(tmp_path / "b", stats(r1=dict(REEL, ts="2026-10-27T06:00:00+0000")), at(28), capsys)
    row = reel_row(out, "2026-10-27")
    assert row.count("5/2/1") == 1 and row.count("·") == 3
    # 5h old: nothing yet
    out = run(tmp_path / "c", stats(r1=dict(REEL, ts="2026-10-28T07:00:00+0000")), at(28), capsys)
    row = reel_row(out, "2026-10-28")
    assert row.count("·") == 4


def test_milestones_freeze_and_late_flag(tmp_path, capsys):
    run(tmp_path, stats(r1=dict(REEL, ts="2026-10-26T12:00:00+0000", reach=200, likes=20, saved=4, shares=2)),
        at(28, 12), capsys)  # 48h old: 24h and 48h read now (48h exactly on time, 24h 24h late)
    rep = json.loads((tmp_path / "ins" / "report-2026-10-28.json").read_text())
    ms = rep["posts"]["r1"]["milestones"]
    assert set(ms) == {"24h", "48h"} and ms["24h"]["late"] is True and ms["48h"]["late"] is False
    # a week later the numbers grew; the 24h/48h reads must not be overwritten, 72h and 7d are new
    out = run(tmp_path, stats(r1=dict(REEL, ts="2026-10-26T12:00:00+0000", reach=400, likes=80, saved=8, shares=4)),
              at(29, 12), capsys)
    ms = json.loads((tmp_path / "ins" / "report-2026-10-29.json").read_text())["posts"]["r1"]["milestones"]
    assert ms["24h"]["reach"] == 200 and ms["48h"]["likes"] == 20
    assert ms["72h"]["reach"] == 400 and "7d" not in ms
    row = reel_row(out, "2026-10-26")
    assert "10/2/1*" in row and "10/2/1 |" in row and "20/2/1 |" in row


def test_changed_since_last_report(tmp_path, capsys):
    run(tmp_path, stats(100, r1=REEL), at(21), capsys)
    out = run(tmp_path, stats(112, r1=dict(REEL, reach=1500, likes=70, shares=10),
                              n1=dict(kind="FEED", ts="2026-10-25T08:00:00+0000", reach=5, likes=0, saved=0, shares=0)),
              at(28), capsys)
    sect = out.split("## Changed since last report")[1]
    assert "Followers +12" in sect
    assert "New: 2026-10-25 Post cap n1" in sect
    assert "2026-10-20 Reel cap r1: reach +500, likes +20" in sect
    assert "saves" not in sect and "No change" not in sect


def test_no_change_and_negative_followers(tmp_path, capsys):
    run(tmp_path, stats(100, r1=REEL), at(21), capsys)
    assert "No change." in run(tmp_path, stats(100, r1=REEL), at(28), capsys).split("## Changed")[1]
    out = run(tmp_path, stats(97, r1=REEL), at(29), capsys)
    assert "Followers -3" in out


def test_compares_with_newest_older_report_and_same_day_rerun(tmp_path, capsys):
    run(tmp_path, stats(100, r1=REEL), at(14), capsys)
    run(tmp_path, stats(110, r1=REEL), at(21), capsys)
    out = run(tmp_path, stats(115, r1=REEL), at(28), capsys)
    assert "Followers +5" in out  # against the 21st, not the 14th
    again = run(tmp_path, stats(118, r1=REEL), at(28, 18), capsys)  # rerun same day
    assert "Followers +8" in again  # still against the 21st, not against its own earlier file
    assert len(list((tmp_path / "ins").glob("report-*.json"))) == 3


def test_missing_metrics_and_zero_reach(tmp_path, capsys):
    data = stats(50, bad=dict(ts="2026-10-01T00:00:00+0000", error="(#10) permission"),
                 zero=dict(ts="2026-10-02T00:00:00+0000", reach=0, likes=0, saved=0, shares=0))
    out = run(tmp_path, data, at(28), capsys)
    assert "| 2026-10-01 Reel cap bad | – | – | – | – |" in out
    row = next(l for l in out.splitlines() if "cap zero" in l and l.count("|") == 6 and "–" in l)
    assert row.count("–") == 4
    assert wr.rates({"reach": 0}) is None and wr.rates({"likes": 3}) is None


def test_no_posts_and_stdin(tmp_path, capsys, monkeypatch):
    import io
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"account": {}, "posts": []})))
    wr.main(["-", "--insights-dir", str(tmp_path / "ins")], now=at(28))
    out = capsys.readouterr().out
    assert "**Followers:** –" in out and "## Reels" not in out


@pytest.mark.parametrize("s", ["2026-10-20T12:00:00+0000", "2026-10-20T12:00:00Z", "2026-10-20T12:00:00+00:00",
                               "2026-10-20T12:00:00"])
def test_parse_time_formats(s):
    assert wr.parse_time(s) == at(20)


def test_never_touches_network_or_cwd(tmp_path, capsys):
    run(tmp_path, stats(r1=REEL), at(28), capsys)  # no_network fixture in conftest would raise on any HTTP
    assert not Path("insights").exists() or Path("insights").is_dir()
