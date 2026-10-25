"""Reel milestone reads: reach, plays, saves and shares as rates per reach, at 24 h, 48 h, 72 h and 7 d after posting.
Pure functions, no network and no secrets. stats.py passes in the posts it already read; nothing is stored.
Instagram insights are lifetime totals at the moment of reading, so each entry carries the real age_hours of the read.
A milestone is due while a Reel's age is in [milestone, milestone + WINDOW_HOURS). Runs overlap on purpose
(GitHub starts scheduled runs late and may skip one), so the reader keeps one entry per (id, milestone).
"""
from datetime import datetime, timezone

MILESTONES = {"24h": 24, "48h": 48, "72h": 72, "7d": 168}
WINDOW_HOURS = 8  # a little wider than the 6 h schedule in stats.yml


def parse_time(stamp):
    return datetime.strptime(stamp.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")


def rate(count, reach):
    return round(count / reach, 4) if isinstance(count, (int, float)) and isinstance(reach, (int, float)) and reach > 0 else None


def reel_milestones(posts, now=None):
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
