"""Read-only @noknyc numbers: followers, and per post views, reach, saves, shares, likes, comments, AI label.
Also, per Reel: ig_reels_avg_watch_time and ig_reels_video_view_total_time (milliseconds, as Instagram sends them).
Also, for the account: profile_views and follows (the follows_and_unfollows metric, day period, total value).
Also reel_milestones: rates per reach for Reels at 24 h, 48 h, 72 h and 7 d (see milestones.py).

Use:  IG_TOKEN=... python stats.py            (prints JSON to stdout; the stats workflow encrypts it)
      python stats.py --seen-file path.json   (where the read-once milestone ids are kept)

Each new metric is asked for on its own, so one that Instagram refuses (a RuntimeError from publish.call:
wrong post type, too few views, a Graph API version without it) is skipped and nothing else is lost.
A skipped metric is logged to stderr and listed in the output under "skipped_metrics" as "<id or account>:<metric>".
A metric that is simply missing from an answer, or was skipped, has no key in the output (its milestone
entry shows null); only refusals are counted in "skipped_metrics".
The Graph API version is whatever publish.API points at (graph.instagram.com, no version in the URL),
so what is supported depends on the app's default version; the output shows what it gave.
No network except through publish.call. No secrets are printed.
Exit codes: 0 = JSON printed (even if some metrics were skipped); 2 = bad command line.
"""
import argparse, json, os, sys
import publish
from milestones import reel_milestones, record_reads

SEEN_FILE = os.environ.get("MILESTONES_SEEN", "milestones-seen.json")  # ids only, no numbers, no secrets

METRICS = "views,reach,saved,shares,likes,comments,total_interactions"
REEL_METRICS = ("ig_reels_avg_watch_time", "ig_reels_video_view_total_time")
# output name -> (Instagram metric name, extra request params); account level, day period, single total
ACCOUNT_METRICS = {
    "profile_views": ("profile_views", {"period": "day", "metric_type": "total_value"}),
    "follows": ("follows_and_unfollows", {"period": "day", "metric_type": "total_value"}),
}


def metric_value(answer, name):
    """Value of `name` in an insights answer, from either the values[] or total_value shape. None if absent."""
    for d in (answer or {}).get("data", []):
        if isinstance(d, dict) and d.get("name") == name:
            if isinstance(d.get("total_value"), dict):
                return d["total_value"].get("value")
            vals = d.get("values") or [{}]
            return vals[0].get("value")
    return None


def read_metric(path, name, skipped, label, **params):
    """One metric on its own. Refused -> logged, recorded in `skipped`, None back. Absent -> None, not logged."""
    try:
        return metric_value(publish.call("GET", path, metric=name, **params), name)
    except RuntimeError as e:
        skipped.append(f"{label}:{name}")
        print(f"stats: skipped {label}:{name}: {str(e)[:120]}", file=sys.stderr)
        return None


def reel_watch_metrics(media_id, skipped):
    return {name: read_metric(f"{media_id}/insights", name, skipped, media_id) for name in REEL_METRICS}


def account_metrics(skipped):
    return {out: read_metric("me/insights", name, skipped, "account", **extra)
            for out, (name, extra) in ACCOUNT_METRICS.items()}


def collect(skipped):
    me = publish.call("GET", "me", fields="username,followers_count,media_count")
    me.update({k: v for k, v in account_metrics(skipped).items() if v is not None})  # absent or refused: key left out
    posts = []
    url_fields = "id,caption,media_type,media_product_type,timestamp,permalink,is_ai_generated"
    for m in publish.call("GET", "me/media", fields=url_fields, limit="50").get("data", []):
        try:
            ins = {d["name"]: d["values"][0]["value"] for d in publish.call("GET", f"{m['id']}/insights", metric=METRICS)["data"]}
        except RuntimeError as e:
            ins = {"error": str(e)[:200]}
        if m.get("media_product_type") == "REELS":
            ins.update({k: v for k, v in reel_watch_metrics(m["id"], skipped).items() if v is not None})
        m["caption"] = (m.get("caption") or "")[:60]
        try:
            m["comments_list"] = [[c.get("username"), (c.get("text") or "")[:120]] for c in publish.call("GET", f"{m['id']}/comments", fields="text,username").get("data", [])]
        except RuntimeError as e:
            m["comments_list"] = str(e)[:120]
        posts.append({**m, **ins})
    return me, posts


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seen-file", default=SEEN_FILE, help="read-once milestone ids (default: $MILESTONES_SEEN or milestones-seen.json)")
    args = ap.parse_args(argv)
    skipped = []
    me, posts = collect(skipped)
    try:
        with open(args.seen_file, encoding="utf-8") as f:
            seen = set(json.load(f))
    except (OSError, ValueError, TypeError):
        seen = set()
    due = reel_milestones(posts, seen=seen)
    try:
        with open(args.seen_file, "w", encoding="utf-8") as f:
            json.dump(sorted(record_reads(seen, due)), f)
    except OSError:
        pass  # never lose the stats over the bookkeeping file
    print(json.dumps({"account": me, "posts": posts, "reel_milestones": due, "skipped_metrics": skipped}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
