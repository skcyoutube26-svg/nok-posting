"""One-screen weekly report from the decrypted stats JSON (what tools/read_stats.sh prints).
Local only: reads files, writes files, never touches the network or any key.

Use:  sh tools/read_stats.sh > insights/stats.json
      python tools/week_report.py insights/stats.json
      (or: sh tools/read_stats.sh | python tools/week_report.py -)

Prints the markdown report and writes insights/report-YYYY-MM-DD.json. The next run compares
against the newest earlier report-*.json in the same folder (--insights-dir to change it).
Reels are read at 24h / 48h / 72h / 7d after posting: the first report taken at or after each
milestone is kept for good, and shown as likes / saves / shares per 100 reach.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

MILESTONES = (("24h", 24), ("48h", 48), ("72h", 72), ("7d", 168))
LATE_AFTER_H = 12  # a milestone read this many hours past its mark is flagged with *
METRICS = ("reach", "likes", "saved", "shares")


def parse_time(s):
    s = str(s)
    if len(s) > 5 and s[-5] in "+-" and s[-3] != ":":
        s = s[:-2] + ":" + s[-2:]  # Graph API writes +0000, fromisoformat wants +00:00
    t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def is_reel(p):
    return p.get("media_product_type") == "REELS"


def label(p):
    cap = " ".join((p.get("caption") or "").split())[:24]
    return f"{str(p.get('timestamp', ''))[:10]} {'Reel' if is_reel(p) else 'Post'} {cap}".strip()


def rates(snap):
    """likes, saves, shares as percent of reach; None when reach is missing or zero."""
    reach = snap.get("reach")
    if not reach:
        return None
    return [round(100.0 * (snap.get(k) or 0) / reach, 1) for k in ("likes", "saved", "shares")]


def build_report(stats, prev, now, same_day=None):
    """New report dict from the stats JSON, the previous report (or None) and the current time.
    same_day is an earlier report written today, only used to keep its frozen milestones."""
    prev_posts = (prev or {}).get("posts", {})
    today_posts = (same_day or {}).get("posts", {})
    posts = {}
    for p in stats.get("posts", []):
        pid = str(p["id"])
        snap = {k: num(p.get(k)) for k in METRICS}
        entry = {"label": label(p), "reel": is_reel(p), "timestamp": p.get("timestamp"), **snap,
                 "milestones": {**prev_posts.get(pid, {}).get("milestones", {}),
                                      **today_posts.get(pid, {}).get("milestones", {})}}
        if entry["reel"] and p.get("timestamp") and snap["reach"] is not None:
            age = (now - parse_time(p["timestamp"])).total_seconds() / 3600
            for name, hours in MILESTONES:
                if age >= hours and name not in entry["milestones"]:
                    entry["milestones"][name] = {**snap, "age_h": round(age, 1),
                                                 "late": age - hours > LATE_AFTER_H}
        posts[pid] = entry
    return {"generated": now.isoformat(), "followers": num(stats.get("account", {}).get("followers_count")),
            "posts": posts}


def diff(prev, cur):
    """What moved since the previous report. Empty prev means first report."""
    if prev is None:
        return None
    out = {"followers": None, "new": [], "moved": []}
    if cur["followers"] is not None and prev.get("followers") is not None:
        out["followers"] = cur["followers"] - prev["followers"]
    for pid, c in cur["posts"].items():
        old = prev.get("posts", {}).get(pid)
        if old is None:
            out["new"].append(c["label"])
            continue
        d = {k: c[k] - old[k] for k in METRICS if c[k] is not None and old.get(k) is not None}
        if any(d.values()):
            out["moved"].append((c["label"], d))
    return out


def fmt(v):
    return "–" if v is None else f"{v:,}"


def signed(v):
    return f"{v:+,}"


def cell(m):
    r = rates(m)
    if r is None:
        return "–"
    return "/".join(f"{x:g}" for x in r) + ("*" if m.get("late") else "")


def render(report, change):
    L = [f"# Week report {report['generated'][:10]}", "", f"**Followers:** {fmt(report['followers'])}", ""]
    posts = list(report["posts"].values())
    L += ["## Posts", "", "| Post | Reach | Likes | Saves | Shares |", "|---|--:|--:|--:|--:|"]
    for e in sorted(posts, key=lambda e: str(e["timestamp"]), reverse=True):
        L.append(f"| {e['label']} | {fmt(e['reach'])} | {fmt(e['likes'])} | {fmt(e['saved'])} | {fmt(e['shares'])} |")
    reels = [e for e in posts if e["reel"]]
    if reels:
        L += ["", "## Reels: likes / saves / shares per 100 reach", "",
              "| Reel | " + " | ".join(n for n, _ in MILESTONES) + " |", "|---|" + "--:|" * len(MILESTONES)]
        for e in sorted(reels, key=lambda e: str(e["timestamp"]), reverse=True):
            L.append(f"| {e['label']} | " + " | ".join(cell(e["milestones"][n]) if n in e["milestones"] else "·"
                                                       for n, _ in MILESTONES) + " |")
        L += ["", "`·` not reached yet · `*` first read was over 12h past the mark"]
    L += ["", "## Changed since last report", ""]
    lines = []
    if change is not None:
        if change["followers"]:
            lines.append(f"- Followers {signed(change['followers'])}")
        lines += [f"- New: {n}" for n in change["new"]]
        names = {"reach": "reach", "likes": "likes", "saved": "saves", "shares": "shares"}
        for lab, d in change["moved"]:
            lines.append(f"- {lab}: " + ", ".join(f"{names[k]} {signed(v)}" for k, v in d.items() if v))
    if change is None:
        L.append("First report: nothing to compare yet.")
    else:
        L += lines or ["No change."]
    return "\n".join(L) + "\n"


def find_prev(folder, out_name):
    older = sorted(f for f in folder.glob("report-*.json") if f.name != out_name)
    older = [f for f in older if f.name < out_name]
    return json.loads(older[-1].read_text(encoding="utf-8")) if older else None


def main(argv=None, now=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("stats", help="decrypted stats JSON file, or - for stdin")
    ap.add_argument("--insights-dir", default="insights")
    args = ap.parse_args(argv)
    now = now or datetime.now(timezone.utc)
    raw = sys.stdin.read() if args.stats == "-" else Path(args.stats).read_text(encoding="utf-8")
    stats = json.loads(raw)
    folder = Path(args.insights_dir)
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"report-{now:%Y-%m-%d}.json"
    prev = find_prev(folder, out.name)
    same_day = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
    report = build_report(stats, prev, now, same_day)
    change = diff(prev, report)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    sys.stdout.write(render(report, change))


if __name__ == "__main__":
    main()
