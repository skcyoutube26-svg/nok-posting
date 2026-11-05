"""reel_reads on fixture JSON written here. No network, no keys, nothing outside tmp_path."""
import csv
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import reel_reads as rr  # noqa: E402

NOW = "2026-10-25T12:00:00+00:00"


def reel(link, ts, **kw):
    return {"id": link[-1], "media_product_type": "REELS", "permalink": link, "timestamp": ts,
            "reach": 1000, "views": 2500, "likes": 50, "saved": 20, "shares": 10,
            "ig_reels_avg_watch_time": 6000, **kw}


def run(tmp_path, posts, *extra, name="stats.json"):
    f = tmp_path / name
    f.write_text(json.dumps({"account": {}, "posts": posts}), encoding="utf-8")
    return rr.main([str(f), "--csv", str(tmp_path / "out" / "reads.csv"), "--now", NOW, *extra])


def rows(tmp_path):
    with (tmp_path / "out" / "reads.csv").open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_marks(tmp_path, capsys):
    posts = [reel("https://i/a", "2026-10-24T06:00:00+0000"),    # 30 h  -> 24h
             reel("https://i/b", "2026-10-22T06:00:00+0000"),    # 78 h  -> 72h
             reel("https://i/c", "2026-10-18T06:00:00+0000"),    # 174 h -> 7d
             reel("https://i/d", "2026-10-25T06:00:00+0000"),    # 6 h   -> none
             reel("https://i/e", "2026-10-23T06:00:00+0000"),    # 54 h  -> none
             {**reel("https://i/f", "2026-10-24T06:00:00+0000"), "media_product_type": "FEED"}]
    assert run(tmp_path, posts) == 0
    got = {r["permalink"]: r["mark"] for r in rows(tmp_path)}
    assert got == {"https://i/a": "24h", "https://i/b": "72h", "https://i/c": "7d"}
    assert "views/reach 2.5" in capsys.readouterr().out


def test_rerun_skips_recorded_mark_but_adds_new_one(tmp_path, capsys):
    assert run(tmp_path, [reel("https://i/a", "2026-10-24T06:00:00+0000")]) == 0
    assert run(tmp_path, [reel("https://i/a", "2026-10-24T06:00:00+0000")]) == 0
    assert len(rows(tmp_path)) == 1
    assert "24h already recorded" in capsys.readouterr().err
    f = tmp_path / "stats.json"
    f.write_text(json.dumps([reel("https://i/a", "2026-10-22T06:00:00+0000")]), encoding="utf-8")  # bare list, now 72h
    assert rr.main([str(f), "--csv", str(tmp_path / "out" / "reads.csv"), "--now", NOW]) == 0
    assert [r["mark"] for r in rows(tmp_path)] == ["24h", "72h"]


def test_lengths_and_missing_numbers(tmp_path, capsys):
    (tmp_path / "len.json").write_text(json.dumps({"https://i/a": 12}), encoding="utf-8")
    posts = [reel("https://i/a", "2026-10-24T06:00:00+0000"),
             reel("https://i/b", "2026-10-24T07:00:00+0000", reach=None, ig_reels_avg_watch_time=None)]
    assert run(tmp_path, posts, "--lengths", str(tmp_path / "len.json")) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].endswith("avg-watch/length 0.5")        # 6 s of 12 s
    assert lines[1].endswith("views/reach n/a avg-watch/length n/a")
    assert rows(tmp_path)[1]["avg_watch_ms"] == ""


def test_stdin_and_bad_input(tmp_path, capsys):
    csv_path = str(tmp_path / "r.csv")
    data = io.StringIO(json.dumps({"posts": [reel("https://i/a", "2026-10-24T06:00:00+0000")]}))
    assert rr.main(["--csv", csv_path, "--now", NOW], stdin=data) == 0
    assert rr.main(["--csv", csv_path, "--now", NOW], stdin=io.StringIO("not json")) == 2
    assert rr.main([str(tmp_path / "missing.json"), "--csv", csv_path]) == 2
    assert "ERROR" in capsys.readouterr().err
