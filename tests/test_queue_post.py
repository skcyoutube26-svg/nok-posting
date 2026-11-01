"""tools/queue_post.py with git, gh, HTTP and post_check mocked. Everything lives in tmp_path."""
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import make_queue_entry as mqe  # noqa: E402
import queue_post as qp  # noqa: E402

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
LATER = "2026-11-02T17:00:00Z"
WORDS = 'Owner, 1 Nov 2026: "queue it"'
SHA1, SHA2 = "a" * 40, "b" * 40


class FakeSubprocess:
    TimeoutExpired = subprocess.TimeoutExpired

    def __init__(self):
        self.result = subprocess.CompletedProcess([], 0, "all OK\n", "")

    def run(self, cmd, **kw):
        return self.result


class World:
    """Fake git + gh + HTTP sharing one event log, so the order can be asserted."""

    def __init__(self, root):
        self.root, self.log = root, []
        self.branch = "main"
        self.head = SHA1
        self.runs = []          # what `gh run list` returns, as a list of lists (one per call); last repeats
        self.http = (200, "image/jpeg")
        self.pushes = 0
        self.t = NOW

    def cmd(self, cmd):
        if cmd[0] == "gh":
            self.log.append("gh runs")
            snap = self.runs.pop(0) if len(self.runs) > 1 else (self.runs[0] if self.runs else [])
            return 0, json.dumps(snap), ""
        sub = cmd[3]
        if sub == "rev-parse":
            return 0, (self.branch if "--abbrev-ref" in cmd else self.head) + "\n", ""
        if sub == "add":
            return 0, "", ""
        if sub == "diff":
            return 0, "staged\n", ""
        if sub == "commit":
            self.log.append("commit " + cmd[5].split(":")[0])
            self.head = SHA2 if self.pushes else SHA1
            return 0, "", ""
        if sub == "push":
            self.pushes += 1
            self.log.append("push " + ("media" if self.pushes == 1 else "entry"))
            return 0, "", ""
        raise AssertionError(cmd)

    def fetch(self, url):
        self.log.append("http")
        return self.http

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += timedelta(seconds=s)


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(mqe, "subprocess", FakeSubprocess())
    monkeypatch.delenv("POST_CHECK", raising=False)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "rug.jpg").write_bytes(b"\xff\xd8fake")
    (tmp_path / "src" / "cap.txt").write_text("Thirty rugs.\n", encoding="utf-8")
    (tmp_path / "post_check.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    w = World(tmp_path)
    w.runs = [[{"databaseId": 1, "status": "completed", "conclusion": "success", "headSha": "old"}],
              [{"databaseId": 2, "status": "completed", "conclusion": "success", "headSha": SHA1}]]
    real_make = mqe.make

    def logged_make(a, **kw):
        if a.write:
            w.log.append("write")
        return real_make(a, **kw)
    monkeypatch.setattr(mqe, "make", logged_make)
    return w


def args(w, *extra):
    t = w.root
    return qp.parser().parse_args(["--media", str(t / "src" / "rug.jpg"), "--caption-file", str(t / "src" / "cap.txt"),
                                   "--publish-at", LATER, "--approved", WORDS, "--pages-dir", str(t / "site"),
                                   "--queue-dir", str(t / "q"), "--post-check", str(t / "post_check.sh"),
                                   "--pages-base", "https://x.example/p/", *extra])


def go(w, *extra):
    return qp.queue_post(args(w, *extra), run=w.cmd, fetch_fn=w.fetch, sleep=w.sleep, clock=w.clock, now=NOW)


def test_order_push_media_wait_run_wait_200_write_push_entry(world):
    entry = go(world)
    steps = [e for e in world.log if e != "gh runs"]
    assert steps == ["commit media", "push media", "http", "write", "http", "commit queue", "push entry"]
    # strict order of the milestones:
    idx = {k: world.log.index(k) for k in ("push media", "write", "push entry")}
    assert idx["push media"] < idx["write"] < idx["push entry"]
    first_http = world.log.index("http")
    assert idx["push media"] < first_http < idx["write"]
    last_runs_before_http = max(i for i, e in enumerate(world.log) if e == "gh runs" and i < first_http)
    assert idx["push media"] < last_runs_before_http  # the run was polled after the media push, before the 200 check
    assert Path(entry).is_file()
    assert (world.root / "site" / "media" / "rug.jpg").is_file()


def test_waits_for_active_deploy_before_pushing(world):
    world.runs = [[{"status": "in_progress", "conclusion": None, "headSha": "old"}],
                  [{"status": "completed", "conclusion": "success", "headSha": "old"}],
                  [{"status": "completed", "conclusion": "success", "headSha": SHA1}]]
    go(world)
    assert world.log.index("gh runs") < world.log.index("push media")
    assert world.log[:2] == ["gh runs", "gh runs"]  # still busy once, then idle: only then the commit
    assert world.log[2] == "commit media"


def test_post_check_fail_refuses_before_anything(world, monkeypatch):
    mqe.subprocess.result = subprocess.CompletedProcess([], 0, "  FAIL  check caption\n", "")
    with pytest.raises(qp.Refused, match="post_check failed"):
        go(world)
    assert world.log == []
    assert not (world.root / "site").exists()


def test_post_check_nonzero_exit_refuses(world):
    mqe.subprocess.result = subprocess.CompletedProcess([], 1, "boom\n", "")
    with pytest.raises(qp.Refused):
        go(world)
    assert world.log == []


def test_no_post_check_path_refuses(world):
    a = args(world)
    a.post_check = None
    with pytest.raises(qp.Refused, match="post_check"):
        qp.queue_post(a, run=world.cmd, fetch_fn=world.fetch, now=NOW)
    assert world.log == []


def test_pages_timeout_never_pushes_entry(world):
    world.http = (404, "text/html")
    with pytest.raises(qp.Refused, match="never answered"):
        go(world, "--max-wait-minutes", "1", "--poll-seconds", "20")
    assert "push media" in world.log
    assert "push entry" not in world.log and "write" not in world.log
    assert not list((world.root / "q").glob("*.json")) if (world.root / "q").exists() else True


def test_wrong_content_type_counts_as_not_live(world):
    world.http = (200, "text/html")
    with pytest.raises(qp.Refused):
        go(world, "--max-wait-minutes", "1")
    assert "push entry" not in world.log


def test_deploy_run_failure_stops_before_http(world):
    world.runs = [[], [{"status": "completed", "conclusion": "failure", "headSha": SHA1}]]
    with pytest.raises(qp.Refused, match="failure"):
        go(world)
    assert "http" not in world.log and "push entry" not in world.log


def test_deploy_run_timeout_stops_before_http(world):
    world.runs = [[], [{"status": "in_progress", "conclusion": None, "headSha": SHA1}]]
    with pytest.raises(qp.Refused, match="Pages deploy"):
        go(world, "--run-max-minutes", "1", "--run-poll-seconds", "30")
    assert "http" not in world.log and "push entry" not in world.log


def test_wrong_branch_refuses(world):
    world.branch = "feature"
    with pytest.raises(qp.Refused, match="feature"):
        go(world)
    assert "push media" not in world.log


def test_dry_run_prints_plan_and_touches_nothing(world, capsys):
    go(world, "--dry-run")
    out = capsys.readouterr().out
    assert "PLAN (dry run)" in out and "nothing copied" in out
    assert world.log == []
    assert not (world.root / "site").exists() and not (world.root / "q").exists()


def test_dry_run_still_refuses_on_post_check_fail(world):
    mqe.subprocess.result = subprocess.CompletedProcess([], 0, "  FAIL  x\n", "")
    with pytest.raises(qp.Refused):
        go(world, "--dry-run")


def test_main_exit_codes(world, monkeypatch, capsys):
    monkeypatch.setattr(qp, "queue_post", lambda a: (_ for _ in ()).throw(qp.Refused("nope")))
    assert qp.main(["--media", "x.jpg", "--publish-at", LATER, "--approved", WORDS]) == 2
    assert "REFUSED: nope" in capsys.readouterr().err
    monkeypatch.setattr(qp, "queue_post", lambda a: None)
    assert qp.main(["--media", "x.jpg", "--publish-at", LATER, "--approved", WORDS]) == 0
