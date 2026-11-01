"""week_report: UTF-8 output and the reach-since-last-report column. Synthetic data only."""
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import week_report as wr  # noqa: E402

CAPTION = "It’s a “quiet” week – café — done"


def stats(reach, caption=CAPTION, extra=()):
    posts = [{"id": "p1", "caption": caption, "media_product_type": "FEED",
              "timestamp": "2026-10-20T12:00:00+0000", "reach": reach, "likes": 5, "saved": 1, "shares": 0}]
    posts += list(extra)
    return {"account": {"followers_count": 10}, "posts": posts}


def write_report(folder, name, reach, pid="p1"):
    folder.mkdir(exist_ok=True)
    (folder / name).write_text(json.dumps({
        "generated": "x", "followers": 9,
        "posts": {pid: {"label": "l", "reel": False, "timestamp": "t", "reach": reach, "likes": 1,
                        "saved": 0, "shares": 0, "milestones": {}}}}), encoding="utf-8")


def test_utf8_on_cp1252_stdout(tmp_path, monkeypatch):
    """Simulate a Windows console: stdout would encode as cp1252; main must switch it to UTF-8."""
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252", errors="replace"))
    f = tmp_path / "stats.json"
    f.write_text(json.dumps(stats(100), ensure_ascii=False), encoding="utf-8")
    wr.main([str(f), "--insights-dir", str(tmp_path / "ins")], now=datetime(2026, 10, 25, tzinfo=timezone.utc))
    sys.stdout.flush()
    out = raw.getvalue()
    assert "’".encode("utf-8") in out and "–".encode("utf-8") in out
    assert b"\xef\xbf\xbd" not in out  # no U+FFFD
    written = (tmp_path / "ins" / "report-2026-10-25.json").read_bytes()
    assert CAPTION[:20].encode("utf-8") in written  # written as real characters, not \u escapes


def test_stdin_utf8(tmp_path, monkeypatch, capsys):
    data = json.dumps(stats(100), ensure_ascii=False).encode("utf-8")
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(data), encoding="cp1252"))
    wr.main(["-", "--insights-dir", str(tmp_path / "ins")], now=datetime(2026, 10, 25, tzinfo=timezone.utc))
    assert "“quiet”" in capsys.readouterr().out


def test_reach_delta_between_two_reports(tmp_path, capsys):
    ins = tmp_path / "ins"
    write_report(ins, "report-2026-10-18.json", 100)
    write_report(ins, "report-2026-10-19.json", 150)  # newest earlier one wins
    f = tmp_path / "stats.json"
    new = {"id": "p2", "caption": "new", "media_product_type": "FEED",
           "timestamp": "2026-10-24T12:00:00+0000", "reach": 40, "likes": 0, "saved": 0, "shares": 0}
    f.write_text(json.dumps(stats(175, extra=[new])), encoding="utf-8")
    wr.main([str(f), "--insights-dir", str(ins)], now=datetime(2026, 10, 25, tzinfo=timezone.utc))
    out = capsys.readouterr().out
    assert "| Post | Reach | Likes | Saves | Shares | Reach + |" in out
    row = next(l for l in out.splitlines() if l.startswith("| 2026-10-20"))
    assert row.split("|")[2] == " 175 " and row.split("|")[6] == " +25 "
    new_row = next(l for l in out.splitlines() if l.startswith("| 2026-10-24"))
    assert new_row.split("|")[6] == " – "  # not in previous report
    saved = json.loads((ins / "report-2026-10-25.json").read_text(encoding="utf-8"))
    assert saved["posts"]["p1"]["reach_delta"] == 25


def test_first_report_has_no_delta(tmp_path, capsys):
    f = tmp_path / "stats.json"
    f.write_text(json.dumps(stats(100)), encoding="utf-8")
    wr.main([str(f), "--insights-dir", str(tmp_path / "ins")], now=datetime(2026, 10, 25, tzinfo=timezone.utc))
    row = next(l for l in capsys.readouterr().out.splitlines() if l.startswith("| 2026-10-20"))
    assert row.split("|")[6] == " – "
