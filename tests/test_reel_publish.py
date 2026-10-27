"""Reel path of publish.publish(): container, status polling, timeout, AI label read-back, share_to_feed.
All HTTP is mocked (publish.call is replaced); the conftest network guard stays on."""
import json
import urllib.error

import pytest

import publish

REEL = {"type": "REELS", "file": "media/my rug.mp4", "caption": "Thirty rugs."}


class ScriptedApi:
    """Stands in for publish.call. `statuses` is the list of status_code answers, one per poll."""

    def __init__(self, statuses, readback=None):
        self.statuses = list(statuses)
        self.readback = readback
        self.calls = []

    def __call__(self, method, path, **params):
        self.calls.append((method, path, params))
        if (method, path) == ("POST", "me/media"):
            return {"id": "c1"}
        if (method, path) == ("GET", "c1"):
            return {"status_code": self.statuses.pop(0)}
        if (method, path) == ("POST", "me/media_publish"):
            return {"id": "m1"}
        if (method, path) == ("GET", "m1"):
            if isinstance(self.readback, Exception):
                raise self.readback
            return self.readback if self.readback is not None else {"is_ai_generated": True, "permalink": "https://instagram.invalid/reel/x/"}
        raise AssertionError(f"unexpected call {method} {path}")

    def paths(self):
        return [(m, p) for m, p, _ in self.calls]

    def params(self, method, path):
        return [x[2] for x in self.calls if (x[0], x[1]) == (method, path)][0]


@pytest.fixture
def sleeps(monkeypatch):
    slept = []
    monkeypatch.setattr(publish.time, "sleep", slept.append)
    return slept


def install(monkeypatch, api):
    monkeypatch.setattr(publish, "call", api)
    return api


# ---- container create ----------------------------------------------------------

def test_container_is_created_with_media_type_reels_and_quoted_url(monkeypatch, sleeps):
    api = install(monkeypatch, ScriptedApi(["FINISHED"]))
    publish.publish(REEL)
    assert api.calls[0][:2] == ("POST", "me/media")
    p = api.params("POST", "me/media")
    assert p["media_type"] == "REELS"
    assert p["video_url"] == "https://example.invalid/pages/media/my%20rug.mp4"
    assert p["caption"] == "Thirty rugs."
    assert "image_url" not in p


def test_publish_is_called_with_the_container_id_after_finished(monkeypatch, sleeps):
    api = install(monkeypatch, ScriptedApi(["FINISHED"]))
    mid, _ = publish.publish(REEL)
    assert mid == "m1"
    assert api.params("POST", "me/media_publish") == {"creation_id": "c1"}
    assert api.paths() == [("POST", "me/media"), ("GET", "c1"), ("POST", "me/media_publish"), ("GET", "m1")]


# ---- status polling ------------------------------------------------------------

def test_polls_until_finished_then_publishes(monkeypatch, sleeps):
    api = install(monkeypatch, ScriptedApi(["IN_PROGRESS", "IN_PROGRESS", "FINISHED"]))
    publish.publish(REEL)
    polls = [c for c in api.calls if c[0] == "GET" and c[1] == "c1"]
    assert len(polls) == 3 and all(c[2] == {"fields": "status_code"} for c in polls)
    assert sleeps == [10, 10]                       # waits between polls, not after FINISHED
    assert api.paths().index(("POST", "me/media_publish")) > api.paths().index(("GET", "c1"))


def test_finished_at_once_does_not_sleep(monkeypatch, sleeps):
    install(monkeypatch, ScriptedApi(["FINISHED"]))
    publish.publish(REEL)
    assert sleeps == []


@pytest.mark.parametrize("bad", ["ERROR", "EXPIRED"])
def test_error_status_raises_and_never_publishes(monkeypatch, sleeps, bad):
    api = install(monkeypatch, ScriptedApi(["IN_PROGRESS", bad]))
    with pytest.raises(RuntimeError, match=f"container c1 {bad}"):
        publish.publish(REEL)
    assert ("POST", "me/media_publish") not in api.paths()


# ---- timeout -------------------------------------------------------------------

def test_timeout_after_60_polls_raises_and_never_publishes(monkeypatch, sleeps):
    api = install(monkeypatch, ScriptedApi(["IN_PROGRESS"] * 60))
    with pytest.raises(RuntimeError, match="still processing after 10 min"):
        publish.publish(REEL)
    assert len([c for c in api.calls if c[1] == "c1"]) == 60
    assert sum(sleeps) == 600
    assert ("POST", "me/media_publish") not in api.paths()


def test_finishing_on_the_last_poll_still_publishes(monkeypatch, sleeps):
    api = install(monkeypatch, ScriptedApi(["IN_PROGRESS"] * 59 + ["FINISHED"]))
    mid, _ = publish.publish(REEL)
    assert mid == "m1" and ("POST", "me/media_publish") in api.paths()


def test_timed_out_reel_stays_in_queue(tmp_path, monkeypatch, sleeps, capsys):
    """Through run_due: a Reel that never finishes is reported FAIL, kept in queue/, media kept."""
    for d in ("queue", "done", "media"):
        (tmp_path / d).mkdir()
    monkeypatch.setattr(publish, "ROOT", tmp_path)
    post = dict(REEL, file="media/r.mp4", publish_at="2020-01-01T00:00:00Z", approved="Owner: ok")
    (tmp_path / "queue" / "r.json").write_text(json.dumps(post), encoding="utf-8")
    (tmp_path / "media" / "r.mp4").write_bytes(b"x")
    api = ScriptedApi(["IN_PROGRESS"] * 60)
    real = api.__call__

    def with_live(method, path, **params):
        if (method, path) == ("GET", "me/media"):
            return {"data": []}
        return real(method, path, **params)
    monkeypatch.setattr(publish, "call", with_live)
    assert publish.run_due(dry=False) is True
    assert "FAIL r.json" in capsys.readouterr().out
    assert (tmp_path / "queue" / "r.json").exists() and (tmp_path / "media" / "r.mp4").exists()
    assert not (tmp_path / "done" / "r.json").exists()


# ---- AI label set and read back ------------------------------------------------

def test_ai_label_is_set_on_the_container(monkeypatch, sleeps):
    api = install(monkeypatch, ScriptedApi(["FINISHED"]))
    publish.publish(REEL)
    assert api.params("POST", "me/media")["is_ai_generated"] == "true"


def test_ai_label_is_read_back_from_the_published_media(monkeypatch, sleeps):
    api = install(monkeypatch, ScriptedApi(["FINISHED"]))
    mid, ai = publish.publish(REEL)
    assert api.params("GET", "m1") == {"fields": "is_ai_generated,permalink"}
    assert ai == {"is_ai_generated": True, "permalink": "https://instagram.invalid/reel/x/"}


def test_readback_reporting_label_off_is_passed_through(monkeypatch, sleeps):
    install(monkeypatch, ScriptedApi(["FINISHED"], readback={"is_ai_generated": False, "permalink": "p"}))
    _, ai = publish.publish(REEL)
    assert ai["is_ai_generated"] is False


def test_failed_readback_does_not_fail_a_live_post(monkeypatch, sleeps):
    api = install(monkeypatch, ScriptedApi(["FINISHED"], readback=RuntimeError("GET m1: 500 boom")))
    mid, ai = publish.publish(REEL)
    assert mid == "m1" and "boom" in ai["read_back_error"]
    assert api.paths().count(("POST", "me/media_publish")) == 1   # never retried


# ---- share_to_feed -------------------------------------------------------------

def test_reel_container_shares_to_feed_as_string_true():
    p = publish.container_params(REEL, "u")
    assert p["share_to_feed"] == "true"


@pytest.mark.parametrize("kind", ["IMAGE", "STORIES"])
def test_share_to_feed_is_only_for_reels(kind):
    assert "share_to_feed" not in publish.container_params({"type": kind}, "m.jpg")


def test_share_to_feed_reaches_the_api_call(monkeypatch, sleeps):
    api = install(monkeypatch, ScriptedApi(["FINISHED"]))
    publish.publish(REEL)
    assert api.params("POST", "me/media")["share_to_feed"] == "true"


def test_thumb_offset_sent_only_when_set(monkeypatch, sleeps):
    assert "thumb_offset" not in publish.container_params(REEL, "u")
    assert publish.container_params(dict(REEL, thumb_offset_ms=2500), "u")["thumb_offset"] == "2500"


# ---- the HTTP wrapper itself, Reel create call ---------------------------------

def test_call_posts_reel_params_form_encoded(monkeypatch):
    seen = {}

    class R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"id": "c1"}'

    def fake(req, timeout):
        seen["req"] = req
        return R()
    monkeypatch.setattr(publish.urllib.request, "urlopen", fake)
    out = publish.call("POST", "me/media", **publish.container_params(REEL, "https://x/y.mp4"))
    body = seen["req"].data.decode()
    assert out == {"id": "c1"} and seen["req"].get_method() == "POST"
    assert "media_type=REELS" in body and "share_to_feed=true" in body and "is_ai_generated=true" in body
    assert "access_token=not-a-real-token" in body


def test_call_turns_http_error_into_runtime_error(monkeypatch):
    def fake(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 400, "bad", {}, __import__("io").BytesIO(b'{"error":"bad video"}'))
    monkeypatch.setattr(publish.urllib.request, "urlopen", fake)
    with pytest.raises(RuntimeError, match="400.*bad video"):
        publish.call("POST", "me/media", media_type="REELS")
