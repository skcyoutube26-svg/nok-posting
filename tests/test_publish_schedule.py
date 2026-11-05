"""Late-run fix (Nov 2026): cron coverage around the posting slots, short waits, done/ re-check. No network."""
import json
import re
from pathlib import Path

import publish

YML = (Path(__file__).resolve().parent.parent / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
SLOTS = [(13, 0), (16, 0), (23, 0)]


def cron_times():
    out = []
    for m in re.finditer(r'^\s*- cron: "([^"]+)"', YML, re.M):
        mins, hours = m.group(1).split()[:2]
        if hours == "*" or "/" in mins or "/" in hours or hours == "3" and mins == "0":
            continue
        out += [(int(h), int(mi)) for h in hours.split(",") for mi in mins.split(",")]
    return sorted(out)


def test_every_slot_has_runs_before_and_after():
    t = [h * 60 + m for h, m in cron_times()]
    for h, m in SLOTS:
        slot = h * 60 + m
        assert any(slot - publish.WAIT_MAX / 60 <= x <= slot for x in t), f"no run just before {h}:{m:02d}"
        after = sorted(x for x in t if x > slot)
        assert after and after[0] - slot <= 10, f"first run after {h}:{m:02d} too late"
        assert all(b - a <= 10 for a, b in zip([x for x in t if slot <= x <= slot + 50], [x for x in t if slot <= x <= slot + 50][1:]))


def test_wait_window_is_short_and_workflow_does_not_cancel():
    assert publish.WAIT_MAX <= 30 * 60
    assert "cancel-in-progress: false" in YML and "group: publish" in YML
    assert "timeout-minutes: 350" not in YML


def test_entry_already_in_done_is_not_posted(repo, fake_api, capsys):
    post = {"publish_at": "2020-01-01T00:00:00Z", "type": "IMAGE", "file": "media/a.jpg", "caption": "c", "approved": "yes"}
    (repo / "media" / "a.jpg").write_bytes(b"x")
    (repo / "queue" / "e.json").write_text(json.dumps(post), encoding="utf-8")
    (repo / "done" / "e.json").write_text(json.dumps(post), encoding="utf-8")
    fake_api.calls.clear()
    publish.run_due(False)
    assert not [c for c in fake_api.calls if c[0] == "POST"]
    assert not (repo / "queue" / "e.json").exists()
    assert "already in done/" in capsys.readouterr().out
