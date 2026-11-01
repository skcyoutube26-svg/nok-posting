"""stats.py watch-time and account metrics on synthetic Instagram answers built here. No network, no real data."""
import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import publish  # noqa: E402
import stats  # noqa: E402

NOW = datetime.now(timezone.utc)  # stats.py reads the real clock
AVG, TOT = "ig_reels_avg_watch_time", "ig_reels_video_view_total_time"


def ts(hours_ago):
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S+0000")


def values(name, v):
    return {"data": [{"name": name, "period": "lifetime", "values": [{"value": v}]}]}


def total(name, v):
    return {"data": [{"name": name, "total_value": {"value": v}}]}


MAIN = ["views", "reach", "saved", "shares", "likes", "comments", "total_interactions"]


class FakeGraph:
    """Answers in the shapes stats.py already reads. `refuse` / `absent` are sets of metric names."""
    def __init__(self, hours_ago=25, refuse=(), absent=()):
        self.refuse, self.absent, self.calls = set(refuse), set(absent), []
        self.media = [{"id": "reel1", "media_type": "VIDEO", "media_product_type": "REELS", "timestamp": ts(hours_ago), "caption": "c"},
                      {"id": "img1", "media_type": "IMAGE", "media_product_type": "FEED", "timestamp": ts(500), "caption": "c"}]

    def __call__(self, method, path, **p):
        self.calls.append((path, p.get("metric")))
        if path == "me":
            return {"username": "synthetic", "followers_count": 10, "media_count": 2}
        if path == "me/media":
            return {"data": [dict(m) for m in self.media]}
        if path.endswith("/comments"):
            return {"data": []}
        metric = p["metric"]
        if metric in self.refuse:
            raise RuntimeError(f"GET {path}: 400 metric {metric} not supported")
        if metric == METRICS_ALL:
            return {"data": [{"name": n, "values": [{"value": 5}]} for n in MAIN]}
        if metric in self.absent:
            return {"data": []}
        vals = {AVG: 4200, TOT: 90000, "profile_views": 7, "follows_and_unfollows": 3}
        return total(metric, vals[metric]) if path == "me/insights" else values(metric, vals[metric])


METRICS_ALL = stats.METRICS


@pytest.fixture
def run(monkeypatch, tmp_path, capsys):
    def go(graph):
        monkeypatch.setattr(publish, "call", graph)
        assert stats.main(["--seen-file", str(tmp_path / "seen.json")]) == 0
        cap = capsys.readouterr()
        return json.loads(cap.out), cap.err
    return go


def reel_of(out):
    return next(p for p in out["posts"] if p["id"] == "reel1")


def test_present_fields_in_output(run):
    out, err = run(FakeGraph())
    r = reel_of(out)
    assert (r[AVG], r[TOT]) == (4200, 90000)
    assert out["account"]["profile_views"] == 7 and out["account"]["follows"] == 3
    assert out["skipped_metrics"] == [] and err == ""
    assert r["reach"] == 5  # existing fields unchanged


def test_not_asked_for_non_reels(run):
    g = FakeGraph()
    out, _ = run(g)
    assert AVG not in out["posts"][1] and ("img1/insights", AVG) not in g.calls


def test_absent_metric_is_left_out_not_skipped(run):
    out, err = run(FakeGraph(absent={AVG, "profile_views"}))
    assert AVG not in reel_of(out) and reel_of(out)[TOT] == 90000
    assert "profile_views" not in out["account"]
    assert out["skipped_metrics"] == [] and err == ""


def test_refused_metric_is_skipped_and_logged(run):
    out, err = run(FakeGraph(refuse={AVG, "follows_and_unfollows"}))
    r = reel_of(out)
    assert AVG not in r and r[TOT] == 90000 and r["likes"] == 5
    assert "follows" not in out["account"] and out["account"]["profile_views"] == 7
    assert out["skipped_metrics"] == ["account:follows_and_unfollows", f"reel1:{AVG}"]
    assert AVG in err and "account:follows_and_unfollows" in err


def test_all_new_refused_keeps_existing_output(run):
    out, _ = run(FakeGraph(refuse={AVG, TOT, "profile_views", "follows_and_unfollows"}))
    assert len(out["skipped_metrics"]) == 4
    assert out["account"]["followers_count"] == 10 and reel_of(out)["views"] == 5


def test_milestone_entry_carries_watch_time(run):
    out, _ = run(FakeGraph(hours_ago=25))
    (m,) = out["reel_milestones"]
    assert m[AVG] == 4200 and m[TOT] == 90000 and m["plays_per_reach"] == 1.0


def test_milestone_entry_null_when_refused(run):
    out, _ = run(FakeGraph(hours_ago=25, refuse={TOT}))
    (m,) = out["reel_milestones"]
    assert m[AVG] == 4200 and m[TOT] is None


def test_metric_value_shapes():
    assert stats.metric_value(values("a", 1), "a") == 1
    assert stats.metric_value(total("a", 2), "a") == 2
    assert stats.metric_value({"data": [{"name": "a"}]}, "a") is None
    assert stats.metric_value(None, "a") is None


@pytest.mark.skipif(not shutil.which("openssl"), reason="openssl CLI not present")
def test_fields_survive_the_workflow_encryption(run, tmp_path):
    out, _ = run(FakeGraph())
    plain, enc = tmp_path / "s.json", tmp_path / "s.enc"
    plain.write_text(json.dumps(out), encoding="utf-8")
    base = ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-pass", "pass:synthetic-test-key"]
    subprocess.run(base + ["-in", str(plain), "-out", str(enc)], check=True)
    back = json.loads(subprocess.run(base + ["-d", "-in", str(enc)], check=True, capture_output=True).stdout)
    assert reel_of(back)[AVG] == 4200 and back["account"]["profile_views"] == 7
