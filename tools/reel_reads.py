"""Appends the 24 h, 72 h and 7 d insight reads of each Reel to insights/reel-reads.csv (Test A).
Local only: reads the stats JSON that tools/read_stats.sh prints; never touches the network or any key.

Use:  sh tools/read_stats.sh | python tools/reel_reads.py
      python tools/reel_reads.py stats.json
      python tools/reel_reads.py stats.json --lengths lengths.json --csv insights/reel-reads.csv

Input: the stats JSON (an object with a "posts" list, or a bare list of posts), from a file or stdin.
Per Reel it uses timestamp, permalink, reach, views, likes, saved, shares, ig_reels_avg_watch_time
(milliseconds, as Instagram sends them). Posts that are not Reels are ignored.

Age (now minus timestamp) picks the mark; a read outside every window is skipped, because the
numbers would not be a clean read of that mark:
  24h   from 24 h up to 48 h      72h   from 72 h up to 120 h      7d   from 168 h up to 240 h
One row per (permalink, mark); a mark already in the CSV is skipped, so re-running is safe.
CSV columns: permalink, mark, timestamp, read_at, age_hours, reach, views, likes, saved, shares,
avg_watch_ms (empty when Instagram sent no number).

--lengths is a JSON object {permalink: video length in seconds}. Where a length is known the
printed line also shows avg-watch/length (average watch time divided by video length).

Printed, one line per new row:  <mark> <permalink> views/reach <x> avg-watch/length <x or n/a>
Other lines (skips, notes) go to stderr. --now (ISO time) fixes the clock for tests.

Exit codes: 0 done (rows may be zero), 2 input, lengths or CSV cannot be read or written.
"""
import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = ROOT / "insights" / "reel-reads.csv"
MARKS = (("24h", 24, 48), ("72h", 72, 120), ("7d", 168, 240))  # name, from hours, up to hours
COLUMNS = ["permalink", "mark", "timestamp", "read_at", "age_hours", "reach", "views", "likes",
           "saved", "shares", "avg_watch_ms"]


def parse_time(s):
    s = str(s)
    if len(s) > 5 and s[-5] in "+-" and s[-3] != ":":
        s = s[:-2] + ":" + s[-2:]  # Graph API writes +0000, fromisoformat wants +00:00
    t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def mark_for(age_hours):
    """Name of the mark whose window holds this age, else None."""
    for name, lo, hi in MARKS:
        if lo <= age_hours < hi:
            return name
    return None


def ratio(a, b):
    return round(a / b, 3) if a is not None and b else None


def fmt(v):
    return "n/a" if v is None else f"{v:g}"


def build_rows(data, now, recorded, lengths=None):
    """(rows, notes): new CSV rows as dicts plus printed lines, and skip notes. recorded is a set of
    (permalink, mark) already in the CSV; it is updated so one run never repeats a row."""
    posts = data.get("posts", []) if isinstance(data, dict) else data
    lengths = lengths or {}
    rows, notes = [], []
    for p in posts:
        if not isinstance(p, dict) or p.get("media_product_type") != "REELS":
            continue
        link = p.get("permalink") or p.get("id")
        if not link:
            notes.append("skip: Reel without permalink or id")
            continue
        try:
            age = (now - parse_time(p["timestamp"])).total_seconds() / 3600
        except (KeyError, ValueError, TypeError):
            notes.append(f"skip {link}: no readable timestamp")
            continue
        mark = mark_for(age)
        if mark is None:
            notes.append(f"skip {link}: age {age:.1f} h is outside every mark window")
            continue
        if (link, mark) in recorded:
            notes.append(f"skip {link}: {mark} already recorded")
            continue
        recorded.add((link, mark))
        row = {"permalink": link, "mark": mark, "timestamp": p["timestamp"], "read_at": now.isoformat(),
               "age_hours": round(age, 1)}
        for k in ("reach", "views", "likes", "saved", "shares"):
            row[k] = num(p.get(k))
        row["avg_watch_ms"] = num(p.get("ig_reels_avg_watch_time"))
        length = num(lengths.get(link))
        watch = ratio(row["avg_watch_ms"], length * 1000) if length else None
        line = f"{mark} {link} views/reach {fmt(ratio(row['views'], row['reach']))} avg-watch/length {fmt(watch)}"
        rows.append((row, line))
    return rows, notes


def read_recorded(path):
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as f:
        return {(r.get("permalink"), r.get("mark")) for r in csv.DictReader(f)}


def append_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        if new:
            w.writeheader()
        w.writerows({k: ("" if r[k] is None else r[k]) for k in COLUMNS} for r in rows)


def main(argv=None, stdin=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("stats", nargs="?", help="stats JSON file (default: read stdin)")
    ap.add_argument("--csv", default=str(DEFAULT_CSV), help="CSV to append to (default: insights/reel-reads.csv)")
    ap.add_argument("--lengths", help="JSON {permalink: video length in seconds}")
    ap.add_argument("--now", help="ISO time to use as now (default: the real clock)")
    args = ap.parse_args(argv)
    try:
        text = Path(args.stats).read_text(encoding="utf-8") if args.stats else (stdin or sys.stdin).read()
        data = json.loads(text)
        lengths = json.loads(Path(args.lengths).read_text(encoding="utf-8")) if args.lengths else {}
        if not isinstance(lengths, dict):
            raise ValueError("--lengths must be a JSON object")
        now = parse_time(args.now) if args.now else datetime.now(timezone.utc)
        path = Path(args.csv)
        rows, notes = build_rows(data, now, read_recorded(path), lengths)
        append_rows(path, [r for r, _ in rows])
    except (OSError, ValueError, TypeError) as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 2
    for _, line in rows:
        print(line)
    for n in notes:
        print(n, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
