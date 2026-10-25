"""Tests for publish.py. Everything is mocked: no network, no real queue/ or done/."""
import json
from datetime import datetime, timedelta, timezone

import pytest

import publish

CAPTION = "Thirty rugs. I knew at the ninth."


def iso(delta_hours):
    return (datetime.now(timezone.utc) + timedelta(hours=delta_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def ig_time(delta_hours):
    """Timestamp the way Instagram returns it, e.g. 2026-10-03T01:00:57+0000."""
    return (datetime.now(timezone.utc) + timedelta(hours=delta_hours)).strftime("%Y-%m-%dT%H:%M:%S+0000")


def add_post(repo, name="a.json", **over):
    post = {"publish_at": iso(-1), "type": "REELS", "file": "media/rug.mp4", "caption": CAPTION,
            "approved": "Owner, typed: looks good"}
    post.update(over)
    (repo / "queue" / name).write_text(json.dumps(post), encoding="utf-8")
    if post.get("file"):
        (repo / post["file"]).write_bytes(b"x")
    return post


# ---- queue parsing -------------------------------------------------------------

def test_container_params_image_has_ai_label_and_alt_text():
    p = publish.container_params({"type": "IMAGE", "caption": "c", "alt_text": "a rug"}, "u")
    assert p == {"is_ai_generated": "true", "image_url": "u", "caption": "c", "alt_text": "a rug"}


def test_container_params_reel_cover_frame_and_feed():
    p = publish.container_params({"type": "REELS", "caption": "c", "thumb_offset_ms": 17000}, "u")
    assert p["media_type"] == "REELS" and p["video_url"] == "u"
    assert p["thumb_offset"] == "17000" and p["share_to_feed"] == "true" and p["is_ai_generated"] == "true"


def test_container_params_story_picks_video_or_image_by_extension():
    assert "video_url" in publish.container_params({"type": "STORIES"}, "m.mp4")
    assert "image_url" in publish.container_params({"type": "STORIES"}, "m.jpg")


def test_container_params_unknown_type_refused():
    with pytest.raises(ValueError):
        publish.container_params({"type": "CAROUSEL"}, "u")


def test_next_due_none_for_empty_queue(repo):
    assert publish.next_due() is None


def test_next_due_ignores_past_and_picks_earliest_future(repo):
    add_post(repo, "past.json", publish_at=iso(-2))
    add_post(repo, "late.json", publish_at=iso(5))
    add_post(repo, "soon.json", publish_at=iso(2))
    nxt = publish.next_due()
    assert abs((nxt - datetime.now(timezone.utc)).total_seconds() - 2 * 3600) < 60


def test_future_post_is_left_alone(repo, fake_api):
    add_post(repo, publish_at=iso(3))
    assert publish.run_due(dry=False) is False
    assert fake_api.calls == [] and (repo / "queue" / "a.json").exists()


def test_due_post_publishes_and_moves_to_done(repo, fake_api, capsys):
    add_post(repo)
    assert publish.run_due(dry=False) is False
    assert not (repo / "queue" / "a.json").exists()
    assert not (repo / "media" / "rug.mp4").exists()
    done = json.loads((repo / "done" / "a.json").read_text(encoding="utf-8"))
    assert done["media_id"] == "media1" and done["is_ai_generated"] is True
    assert "POSTED a.json" in capsys.readouterr().out
    # the media URL built from PAGES_BASE is what gets handed to Meta
    create = [c for c in fake_api.posts() if c[1] == "me/media"][0]
    assert create[2]["video_url"] == "https://example.invalid/pages/media/rug.mp4"


def test_missing_media_file_fails_without_posting(repo, fake_api, capsys):
    add_post(repo)
    (repo / "media" / "rug.mp4").unlink()
    assert publish.run_due(dry=False) is True
    assert fake_api.calls == [] and (repo / "queue" / "a.json").exists()
    assert "FAIL a.json" in capsys.readouterr().out


def test_dry_run_prints_and_makes_no_api_call(repo, fake_api, capsys):
    add_post(repo)
    assert publish.run_due(dry=True) is False
    assert fake_api.calls == [] and (repo / "queue" / "a.json").exists()
    assert "DUE a.json" in capsys.readouterr().out


def test_failed_publish_keeps_post_in_queue(repo, fake_api, monkeypatch, capsys):
    add_post(repo)
    monkeypatch.setattr(publish, "publish", lambda post: (_ for _ in ()).throw(RuntimeError("meta said no")))
    assert publish.run_due(dry=False) is True
    assert (repo / "queue" / "a.json").exists() and not (repo / "done" / "a.json").exists()


# ---- check-live-state-before-posting guard (3 Oct double post of the rugs Reel) -

def test_already_live_true_when_same_caption_posted_recently(fake_api):
    fake_api.live = [{"caption": CAPTION, "timestamp": ig_time(-0.5)}]
    assert publish.already_live({"caption": CAPTION}) is True


def test_already_live_ignores_surrounding_whitespace(fake_api):
    fake_api.live = [{"caption": CAPTION + "\n", "timestamp": ig_time(-1)}]
    assert publish.already_live({"caption": "  " + CAPTION}) is True


def test_already_live_false_for_old_or_different_post(fake_api):
    fake_api.live = [{"caption": CAPTION, "timestamp": ig_time(-13)},
                     {"caption": "something else", "timestamp": ig_time(-1)}]
    assert publish.already_live({"caption": CAPTION}) is False


def test_already_live_false_when_account_has_no_media(fake_api):
    fake_api.live = []
    assert publish.already_live({"caption": CAPTION}) is False


def test_guard_refuses_to_post_when_reel_already_live(repo, fake_api, capsys):
    """The 3 Oct regression: a second run finds the Reel already live and must not post it again."""
    add_post(repo, "2026-10-03T0100Z-reel-ninth-rug.json")
    fake_api.live = [{"caption": CAPTION, "timestamp": ig_time(-0.1)}]
    assert publish.run_due(dry=False) is False
    assert fake_api.posts() == []                      # nothing was sent to Instagram
    assert "double-post guard" in capsys.readouterr().out
    # the queue entry is retired so the next run cannot try again
    assert not (repo / "queue" / "2026-10-03T0100Z-reel-ninth-rug.json").exists()
    assert (repo / "done" / "2026-10-03T0100Z-reel-ninth-rug.json").exists()
    assert (repo / "media" / "rug.mp4").exists()       # media is kept: it was never published by this run


def test_guard_runs_before_any_container_is_created(repo, fake_api):
    add_post(repo)
    fake_api.live = [{"caption": CAPTION, "timestamp": ig_time(-1)}]
    publish.run_due(dry=False)
    assert [c[:2] for c in fake_api.calls] == [("GET", "me/media")]


def test_guard_lets_a_genuinely_new_post_through(repo, fake_api):
    add_post(repo)
    fake_api.live = [{"caption": "an older, different post", "timestamp": ig_time(-1)}]
    publish.run_due(dry=False)
    assert len(fake_api.posts()) == 2                   # container + publish


# ---- gate: owner approval ------------------------------------------------------

@pytest.mark.parametrize("approved", [None, ""])
def test_entry_without_owner_approval_is_refused(repo, fake_api, capsys, approved):
    post = add_post(repo)
    if approved is None:
        post.pop("approved")
    else:
        post["approved"] = approved
    (repo / "queue" / "a.json").write_text(json.dumps(post), encoding="utf-8")
    assert publish.run_due(dry=False) is False
    assert fake_api.calls == []                         # not even the live-state read
    assert (repo / "queue" / "a.json").exists() and not (repo / "done" / "a.json").exists()
    assert "no owner approval" in capsys.readouterr().out


def test_unapproved_entry_is_refused_in_dry_run_too(repo, fake_api, capsys):
    post = add_post(repo)
    post.pop("approved")
    (repo / "queue" / "a.json").write_text(json.dumps(post), encoding="utf-8")
    publish.run_due(dry=True)
    out = capsys.readouterr().out
    assert "SKIP a.json" in out and "DUE" not in out


# ---- gate: post_check ----------------------------------------------------------

@pytest.mark.skip(reason="publish.py has no post_check field or pre-commit gate yet; nothing to test. "
                         "Unskip and fill in once the gate exists in the repo.")
def test_gate_refuses_entry_without_post_check_passing():
    ...


# ---- safety net ----------------------------------------------------------------

def test_tests_cannot_reach_the_network():
    with pytest.raises(AssertionError):
        publish.urllib.request.urlopen("https://graph.instagram.com/me")


def test_real_call_wrapper_uses_only_the_mocked_urlopen(monkeypatch):
    seen = {}

    class R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"ok": true}'

    def fake(req, timeout):
        seen["url"] = req.full_url
        return R()
    monkeypatch.setattr(publish.urllib.request, "urlopen", fake)
    assert publish.call("GET", "me/media", fields="id") == {"ok": True}
    assert seen["url"].startswith("https://graph.instagram.com/me/media?")
