"""Reel milestone reads: reach, plays, saves and shares as rates per reach, at 24 h, 48 h, 72 h and 7 d after posting.
Pure functions, no network and no secrets. stats.py passes in the posts it already read; nothing is stored.
Instagram insights are lifetime totals at the moment of reading, so each entry carries the real age_hours of the read.
A milestone is due while a Reel's age is in [milestone, milestone + WINDOW_HOURS). The window is wider than the
6 h schedule on purpose (GitHub starts scheduled runs late and may skip one), so several runs can fall inside it.

Read-once rule: each milestone (24 h, 48 h, 72 h, 7 d) of a Reel is read exactly once. The first run that finds
the Reel inside the window and gets real numbers keeps that read and records "id:milestone" in the seen set;
every later run in the same window skips it. A read that came back as an error is not recorded, so the next
run in the window may retry it. The caller (stats.py) keeps the seen set between runs; this module stays pure.
"""
from datetime import datetime, timezone

MILESTONES = {"24h": 24, "48h": 48, "72h": 72, "7d": 168}
WINDOW_HOURS = 8  # wider than the 6 h schedule in stats.yml so a late or skipped run still lands inside; the seen set stops double reads


def parse_time(stamp):
    return datetime.strptime(stamp.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")


def rate(count, reach):
    return round(count / reach, 4) if isinstance(count, (int, float)) and isinstance(reach, (int, float)) and reach > 0 else None


def seen_key(entry):
    return f"{entry['id']}:{entry['milestone']}"


def reel_milestones(posts, now=None, seen=()):
    """Due reads for this run, skipping every (id, milestone) already in `seen`. Does not change `seen`."""
    now = now or datetime.now(timezone.utc)
    out = []
    for p in posts:
        try:
            if p.get("media_product_type") != "REELS":
                continue
            age = (now - parse_time(p["timestamp"])).total_seconds() / 3600
            for label, hours in MILESTONES.items():
                if not hours <= age < hours + WINDOW_HOURS:
                    continue
                if f"{p['id']}:{label}" in seen:
                    continue
                entry = {"id": p["id"], "milestone": label, "age_hours": round(age, 1)}
                if "error" in p:
                    entry["error"] = p["error"]
                else:
                    reach = p.get("reach")
                    entry.update(reach=reach, plays_per_reach=rate(p.get("views"), reach),
                                 saves_per_reach=rate(p.get("saved"), reach), shares_per_reach=rate(p.get("shares"), reach))
                out.append(entry)
        except (KeyError, ValueError, TypeError):
            continue  # one bad post must never cost the rest of the stats
    return out


def record_reads(seen, entries):
    """Add this run's successful reads to `seen` (a set) so later runs in the same window skip them."""
    seen.update(seen_key(e) for e in entries if "error" not in e)
    return seen
