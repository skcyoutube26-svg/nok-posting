"""make_queue_entry: Pages base precedence is --pages-base flag, then PAGES_BASE env var, then pages_config.json.
Synthetic config files in tmp_path only; HTTP is mocked."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import make_queue_entry as mqe  # noqa: E402

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
LATER = "2026-11-02T17:00:00Z"
WORDS = 'Owner, 1 Nov 2026: "looks good, queue it"'


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmp_path):
    monkeypatch.delenv("PAGES_BASE", raising=False)
    monkeypatch.delenv("POST_CHECK", raising=False)
    monkeypatch.setattr(mqe, "fetch", lambda url: (200, "image/jpeg"))
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "rug.jpg").write_bytes(b"\xff\xd8fake-jpeg")
    (tmp_path / "src" / "cap.txt").write_text("Thirty rugs.\n", encoding="utf-8")


def set_config(monkeypatch, tmp_path, text):
    path = tmp_path / "pages_config.json"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(mqe, "CONFIG_PATH", path)


def confirm_url(tmp_path, monkeypatch, *flags):
    entry = tmp_path / "e.json"
    entry.write_text(json.dumps({"file": "media/rug.jpg", "publish_at": LATER}), encoding="utf-8")
    seen = []
    a = mqe.parser().parse_args(["confirm", str(entry), *flags])
    mqe.confirm(a, clock=lambda: NOW, fetch_fn=lambda url: seen.append(url) or (200, "image/jpeg"))
    return seen


def make_url(tmp_path, monkeypatch, *flags):
    seen = []
    monkeypatch.setattr(mqe, "fetch", lambda url: seen.append(url) or (200, "image/jpeg"))
    post_check = tmp_path / "pc.sh"
    post_check.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(mqe, "run_post_check", lambda *a, **k: None)
    a = mqe.parser().parse_args(["make", "--media", str(tmp_path / "src" / "rug.jpg"), "--caption-file",
                                 str(tmp_path / "src" / "cap.txt"), "--publish-at", LATER, "--approved", WORDS,
                                 "--pages-dir", str(tmp_path / "site"), "--queue-dir", str(tmp_path / "q"),
                                 "--post-check", str(post_check), "--write", *flags])
    mqe.make(a, now=NOW, clock=lambda: NOW)
    return seen


def test_config_used_when_no_flag_and_no_env(tmp_path, monkeypatch):
    set_config(monkeypatch, tmp_path, '{"pages_base": "https://cfg.github.io/r/"}')
    assert confirm_url(tmp_path, monkeypatch) == ["https://cfg.github.io/r/media/rug.jpg"]
    assert make_url(tmp_path, monkeypatch) == ["https://cfg.github.io/r/media/rug.jpg"]


def test_env_beats_config(tmp_path, monkeypatch):
    set_config(monkeypatch, tmp_path, '{"pages_base": "https://cfg.github.io/r/"}')
    monkeypatch.setenv("PAGES_BASE", "https://env.github.io/r/")
    assert confirm_url(tmp_path, monkeypatch) == ["https://env.github.io/r/media/rug.jpg"]
    assert make_url(tmp_path, monkeypatch) == ["https://env.github.io/r/media/rug.jpg"]


def test_flag_beats_env_and_config(tmp_path, monkeypatch):
    set_config(monkeypatch, tmp_path, '{"pages_base": "https://cfg.github.io/r/"}')
    monkeypatch.setenv("PAGES_BASE", "https://env.github.io/r/")
    flag = ("--pages-base", "https://flag.github.io/r/")
    assert confirm_url(tmp_path, monkeypatch, *flag) == ["https://flag.github.io/r/media/rug.jpg"]
    assert make_url(tmp_path, monkeypatch, *flag) == ["https://flag.github.io/r/media/rug.jpg"]


@pytest.mark.parametrize("text", [None, "", "not json", "[]", '{}', '{"pages_base": ""}', '{"pages_base": 5}'])
def test_missing_or_bad_config_still_refuses(tmp_path, monkeypatch, text):
    if text is None:
        monkeypatch.setattr(mqe, "CONFIG_PATH", tmp_path / "absent.json")
    else:
        set_config(monkeypatch, tmp_path, text)
    with pytest.raises(mqe.Refused, match="--pages-base"):
        confirm_url(tmp_path, monkeypatch)
    with pytest.raises(mqe.Refused, match="--pages-base"):
        make_url(tmp_path, monkeypatch)


def test_config_value_must_be_https(tmp_path, monkeypatch):
    set_config(monkeypatch, tmp_path, '{"pages_base": "http://cfg.github.io/r/"}')
    with pytest.raises(mqe.Refused, match="https"):
        confirm_url(tmp_path, monkeypatch)


def test_shipped_config_is_valid_https():
    base = mqe.config_pages_base(Path(mqe.__file__).resolve().parent.parent / "pages_config.json")
    assert base and base.startswith("https://")
