"""Run from the repo root: python3 -m unittest discover -s tests
Every fixture is made here. Nothing touches the network and no token is needed or read."""
import json, os, re, shutil, subprocess, sys, tempfile, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from milestones import MILESTONES, WINDOW_HOURS, record_reads, reel_milestones  # noqa: E402
from milestones_config import MILESTONES_ENABLED  # noqa: E402

NOW = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)


def stamp(hours_ago):
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S+0000")


def reel(hours_ago, **extra):
    return {"id": "r1", "media_product_type": "REELS", "timestamp": stamp(hours_ago),
            "reach": 200, "views": 500, "saved": 10, "shares": 4, **extra}


class ReadOnceTests(unittest.TestCase):
    def test_two_runs_in_one_window_read_once(self):
        seen = set()
        first = reel_milestones([reel(25)], NOW, seen)
        self.assertEqual([e["milestone"] for e in first], ["24h"])
        record_reads(seen, first)
        later = NOW + timedelta(hours=6)  # next 6 h run, Reel is now 31 h old, still inside 24..32
        self.assertEqual(reel_milestones([reel(31)], later, seen), [])

    def test_first_read_in_window_is_the_one_kept(self):
        seen = set()
        (a,) = reel_milestones([reel(24.5)], NOW, seen)
        record_reads(seen, [a])
        self.assertEqual(a["age_hours"], 24.5)
        self.assertEqual(reel_milestones([reel(30.5)], NOW, seen), [])

    def test_other_milestones_and_reels_still_read(self):
        seen = {"r1:24h"}
        self.assertEqual([e["milestone"] for e in reel_milestones([reel(49)], NOW, seen)], ["48h"])
        self.assertEqual([e["id"] for e in reel_milestones([reel(25, id="r2")], NOW, seen)], ["r2"])

    def test_error_read_is_not_recorded_so_next_run_can_retry(self):
        seen = set()
        err = {"id": "r1", "media_product_type": "REELS", "timestamp": stamp(25), "error": "boom"}
        record_reads(seen, reel_milestones([err], NOW, seen))
        self.assertEqual(seen, set())
        self.assertEqual(len(reel_milestones([reel(31)], NOW, seen)), 1)

    def test_seen_is_not_changed_by_reading(self):
        seen = set()
        reel_milestones([reel(25)], NOW, seen)
        self.assertEqual(seen, set())


class MilestoneTests(unittest.TestCase):
    def test_each_milestone_edges(self):
        for label, h in MILESTONES.items():
            with self.subTest(label=label):
                self.assertEqual(reel_milestones([reel(h - 0.1)], NOW), [])
                self.assertEqual([e["milestone"] for e in reel_milestones([reel(h)], NOW)], [label])
                self.assertEqual([e["milestone"] for e in reel_milestones([reel(h + WINDOW_HOURS - 0.1)], NOW)], [label])
                self.assertEqual(reel_milestones([reel(h + WINDOW_HOURS)], NOW), [])

    def test_four_milestones(self):
        self.assertEqual(list(MILESTONES.values()), [24, 48, 72, 168])

    def test_rates_per_reach(self):
        (e,) = reel_milestones([reel(25)], NOW)
        self.assertEqual((e["id"], e["reach"], e["age_hours"]), ("r1", 200, 25.0))
        self.assertEqual((e["plays_per_reach"], e["saves_per_reach"], e["shares_per_reach"]), (2.5, 0.05, 0.02))

    def test_zero_or_missing_reach_gives_null_rates(self):
        for r in (reel(25, reach=0), {k: v for k, v in reel(25).items() if k != "reach"}):
            (e,) = reel_milestones([r], NOW)
            self.assertEqual((e["plays_per_reach"], e["saves_per_reach"], e["shares_per_reach"]), (None, None, None))

    def test_missing_count_is_null_not_zero(self):
        r = reel(25)
        del r["shares"]
        self.assertIsNone(reel_milestones([r], NOW)[0]["shares_per_reach"])

    def test_non_reels_skipped(self):
        for kind in ("FEED", "STORY", None):
            self.assertEqual(reel_milestones([reel(25, media_product_type=kind)], NOW), [])

    def test_errored_insights_kept_as_error(self):
        (e,) = reel_milestones([{"id": "r1", "media_product_type": "REELS", "timestamp": stamp(25), "error": "boom"}], NOW)
        self.assertEqual(e["error"], "boom")
        self.assertNotIn("reach", e)

    def test_bad_post_does_not_kill_the_others(self):
        posts = [reel(25, timestamp="not a time"), {"media_product_type": "REELS"}, reel(25, reach="x"), reel(49, id="r2")]
        out = reel_milestones(posts, NOW)
        self.assertEqual([e["id"] for e in out], ["r1", "r2"])
        self.assertIsNone(out[0]["plays_per_reach"])

    def test_z_timestamp_also_parses(self):
        r = reel(25, timestamp=stamp(25).replace("+0000", "Z"))
        self.assertEqual(len(reel_milestones([r], NOW)), 1)


STUB_PUBLISH = '''
import json
def call(method, path, **p):
    assert "access_token" not in p
    data = json.load(open("fixture.json"))
    if path == "me":
        return data["me"]
    if path == "me/media":
        return {"data": data["media"]}
    if path.endswith("/insights"):
        return {"data": [{"name": k, "values": [{"value": v}]} for k, v in data["insights"].items()]}
    if path.endswith("/comments"):
        return {"data": []}
    raise RuntimeError("unexpected " + path)
'''


class WiringTests(unittest.TestCase):
    def test_stats_output_keeps_old_keys_and_adds_milestones(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for f in ("stats.py", "milestones.py", "milestones_config.py"):
                shutil.copy(ROOT / f, d / f)
            (d / "publish.py").write_text(STUB_PUBLISH)
            ago = (datetime.now(timezone.utc) - timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%S+0000")  # stats.py reads the real clock
            media = [{"id": "m1", "media_product_type": "REELS", "timestamp": ago},
                     {"id": "m2", "media_product_type": "FEED", "timestamp": ago}]
            (d / "fixture.json").write_text(json.dumps({
                "me": {"username": "fixture"}, "media": media,
                "insights": {"reach": 100, "views": 300, "saved": 5, "shares": 2, "likes": 9, "comments": 1, "total_interactions": 17}}))
            env = {"PATH": os.environ.get("PATH", "")}  # built from scratch: no IG_TOKEN
            r = subprocess.run([sys.executable, "-B", "stats.py"], cwd=d, env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["account"], {"username": "fixture"})
            self.assertEqual([p["id"] for p in out["posts"]], ["m1", "m2"])
            self.assertEqual(out["posts"][0]["reach"], 100)
            (e,) = out["reel_milestones"]
            self.assertEqual((e["id"], e["milestone"], e["plays_per_reach"], e["saves_per_reach"], e["shares_per_reach"]),
                             ("m1", "24h", 3.0, 0.05, 0.02))
            r2 = subprocess.run([sys.executable, "-B", "stats.py"], cwd=d, env=env, capture_output=True, text=True)  # second run, same window
            self.assertEqual(json.loads(r2.stdout)["reel_milestones"], [])
            self.assertEqual(json.loads((d / "milestones-seen.json").read_text()), ["m1:24h"])


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / ".github/workflows/stats.yml").read_text()

    def test_schedule_follows_the_flag(self):
        lines = self.text.splitlines()
        live = [l for l in lines if re.match(r"\s*(schedule:|- cron:)", l)]
        if MILESTONES_ENABLED:
            self.assertEqual(len(live), 2, "flag is on, so the schedule and cron lines must be live")
        else:
            self.assertEqual(live, [], "MILESTONES_ENABLED is False, so no live schedule or cron line")

    def test_flag_is_a_real_bool_and_off_by_default(self):
        self.assertIsInstance(MILESTONES_ENABLED, bool)
        self.assertIn("MILESTONES_ENABLED = False", (ROOT / "milestones_config.py").read_text())

    def test_manual_run_still_there(self):
        self.assertIn("  workflow_dispatch:", self.text)

    def test_key_handling_unchanged(self):
        for line in ("IG_TOKEN: ${{ secrets.IG_TOKEN }}",
                     "python stats.py > s.json",
                     "openssl rand -hex 32 > k",
                     "openssl enc -aes-256-cbc -pbkdf2 -in s.json -out s.enc -pass file:k",
                     "openssl pkeyutl -encrypt -pubin -inkey stats-pub.pem -in k -out k.enc",
                     'echo "KEY:$(base64 -w0 k.enc)"',
                     'echo "DATA:$(base64 -w0 s.enc)"'):
            self.assertIn(line, self.text)

    def test_nothing_publishes(self):
        self.assertNotIn("publish.py", self.text)
        self.assertNotIn("git push", self.text)


if __name__ == "__main__":
    unittest.main()
