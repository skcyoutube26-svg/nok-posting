"""tools/make_queue_entry.py: entry shape, publish.py's own checks, dry run, Pages confirmation.
Everything lives in tmp_path; HTTP is mocked; the conftest network guard stays on."""
import io
import json
import subprocess
import sys
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import make_queue_entry as mqe  # noqa: E402
import publish  # noqa: E402

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
LATER = "2026-11-02T17:00:00Z"
WORDS = 'Owner, 1 Nov 2026: "looks good, queue it"'
TOOL = Path(__file__).resolve().parent.parent / "tools" / "make_queue_entry.py"


@pytest.fixture
def work(tmp_path):
    """src/ holds fixture media and caption; site/ is the Pages folder; q/ the queue."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "rug.jpg").write_bytes(b"\xff\xd8fake-jpeg")
    (tmp_path / "src" / "rug.mp4").write_bytes(b"fake-mp4")
    (tmp_path / "src" / "cap.txt").write_text("Thirty rugs.\n", encoding="utf-8")
    return tmp_path


def args(work, **over):
    base = ["make", "--media", str(work / "src" / "rug.jpg"), "--caption-file", str(work / "src" / "cap.txt"),
            "--publish-at", LATER, "--approved", WORDS, "--pages-dir", str(work / "site"), "--queue-dir", str(work / "q")]
    for k, v in over.items():
        flag = "--" + k.replace("_", "-")
        if k in ("media",):
            base[base.index(flag) + 1] = v
        elif v is True:
            base.append(flag)
        elif v is None:
            i = base.index(flag)
            del base[i:i + 2]
        else:
            if flag in base:
                base[base.index(flag) + 1] = v
            else:
                base += [flag, v]
    return mqe.parser().parse_args(base)


def snapshot(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def run_make(work, now=NOW, **over):
    return mqe.make(args(work, **over), now=now)


# ---- dry run / write ---------------------------------------------------------

def test_dry_run_writes_nothing(work, capsys):
    before = snapshot(work)
    run_make(work)
    assert snapshot(work) == before
    out = capsys.readouterr().out
    assert "DRY RUN: nothing written" in out and '"type": "IMAGE"' in out


def test_write_creates_entry_and_copies_media(work):
    path = run_make(work, write=True)
    assert path == work / "q" / "2026-11-02T1700Z-rug.json"
    assert (work / "site" / "media" / "rug.jpg").read_bytes() == (work / "src" / "rug.jpg").read_bytes()
    entry = json.loads(path.read_text(encoding="utf-8"))
    assert entry == {"publish_at": LATER, "type": "IMAGE", "file": "media/rug.jpg", "caption": "Thirty rugs.", "approved": WORDS}


def test_entry_shape_for_reel_with_hashtags_alt_free_cover(work):
    path = run_make(work, write=True, media=str(work / "src" / "rug.mp4"), hashtags="marblesculpture, #NewYorkCity #marblesculpture",
                    cover_offset_ms="17000")
    entry = json.loads(path.read_text(encoding="utf-8"))
    assert list(entry) == ["publish_at", "type", "file", "thumb_offset_ms", "caption", "approved"]
    assert entry["type"] == "REELS" and entry["thumb_offset_ms"] == 17000
    assert entry["caption"] == "Thirty rugs.\n\n#marblesculpture #NewYorkCity"


def test_image_alt_text_and_unicode_kept_verbatim(work):
    path = run_make(work, write=True, alt_text="A rug, café light", approved="Owner: “ok”")
    raw = path.read_text(encoding="utf-8")
    assert "café" in raw and "“ok”" in raw  # ensure_ascii=False, like done/
    assert json.loads(raw)["alt_text"] == "A rug, café light"


def test_hashtag_already_in_caption_not_repeated(work):
    (work / "src" / "cap.txt").write_text("Rugs #antiquerugs\n", encoding="utf-8")
    entry = json.loads(run_make(work, write=True, hashtags="#AntiqueRugs #new").read_text(encoding="utf-8"))
    assert entry["caption"] == "Rugs #antiquerugs\n\n#new"


def test_story_from_image_needs_no_caption(work):
    (work / "src" / "cap.txt").write_text("", encoding="utf-8")
    entry = json.loads(run_make(work, write=True, type="STORIES").read_text(encoding="utf-8"))
    assert entry["type"] == "STORIES" and entry["caption"] == ""


# ---- round trip through the real publisher ----------------------------------

def test_publisher_accepts_written_entry(work, monkeypatch, capsys):
    """publish.run_due (dry) must say DUE for the entry, not SKIP or FAIL, and next_due must read it."""
    path = run_make(work, write=True, queue_dir=str(work / "site" / "queue"), media=str(work / "src" / "rug.mp4"),
                    cover_offset_ms="5000")
    monkeypatch.setattr(publish, "ROOT", work / "site")
    assert publish.next_due() == datetime(2026, 11, 2, 17, 0, tzinfo=timezone.utc)
    entry = json.loads(path.read_text(encoding="utf-8"))
    entry["publish_at"] = "2020-01-01T00:00:00Z"  # backdate so it is due
    path.write_text(json.dumps(entry), encoding="utf-8")
    capsys.readouterr()
    assert publish.run_due(dry=True) is False
    out = capsys.readouterr().out
    assert out.startswith("DUE") and "SKIP" not in out and "FAIL" not in out and "'thumb_offset': '5000'" in out


def test_default_paths_follow_publish_root(work, monkeypatch):
    monkeypatch.setattr(publish, "ROOT", work / "repo")
    (work / "repo" / "queue").mkdir(parents=True)
    a = args(work, write=True, pages_dir=None, queue_dir=None)
    path = mqe.make(a, now=NOW)
    assert path.parent == work / "repo" / "queue" and (work / "repo" / "media" / "rug.jpg").is_file()


# ---- refusals ----------------------------------------------------------------

@pytest.mark.parametrize("over, fragment", [
    ({"approved": "   "}, "no approval"),
    ({"publish_at": "2026-11-02T17:00:00"}, "explicit UTC"),
    ({"publish_at": "2026-11-02T17:00:00+06:00"}, "explicit UTC"),
    ({"publish_at": "not-a-time"}, "explicit UTC"),
    ({"publish_at": "2026-13-45T99:00:00Z"}, "not a valid"),
    ({"publish_at": "2026-11-01T12:20:00Z"}, "too soon"),
    ({"publish_at": "2026-10-01T12:00:00Z"}, "too soon"),
    ({"alt_text": "x", "media": "VIDEO"}, "alt text only"),
    ({"cover_offset_ms": "100"}, "cover offset only"),
    ({"cover_offset_ms": "-5", "media": "VIDEO"}, "zero or more"),
    ({"type": "REELS"}, "does not fit"),
])
def test_refusals_write_nothing(work, over, fragment):
    if over.get("media") == "VIDEO":
        over = {**over, "media": str(work / "src" / "rug.mp4")}
    before = snapshot(work)
    with pytest.raises(mqe.Refused, match=fragment):
        run_make(work, write=True, **over)
    assert snapshot(work) == before


def test_missing_and_empty_media_refused(work):
    with pytest.raises(mqe.Refused, match="missing or empty"):
        run_make(work, media=str(work / "src" / "nope.jpg"))
    (work / "src" / "empty.jpg").write_bytes(b"")
    with pytest.raises(mqe.Refused, match="missing or empty"):
        run_make(work, media=str(work / "src" / "empty.jpg"))


def test_empty_caption_refused_for_image(work):
    (work / "src" / "cap.txt").write_text("  \n", encoding="utf-8")
    with pytest.raises(mqe.Refused, match="caption is empty"):
        run_make(work)


@pytest.mark.parametrize("name, fragment", [("rug.JPG", "lowercase"), ("rug.gif", "unsupported"), ("my rug.jpg", "media name")])
def test_bad_media_names_refused(work, name, fragment):
    (work / "src" / name).write_bytes(b"x")
    with pytest.raises(mqe.Refused, match=fragment):
        run_make(work, media=str(work / "src" / name))


def test_story_mov_refused(work):
    (work / "src" / "clip.mov").write_bytes(b"x")
    with pytest.raises(mqe.Refused, match="Story video must be .mp4"):
        run_make(work, media=str(work / "src" / "clip.mov"), type="STORIES")


def test_existing_entry_and_different_media_not_overwritten(work):
    run_make(work, write=True)
    with pytest.raises(mqe.Refused, match="already exists"):
        run_make(work, write=True)
    (work / "q" / "2026-11-02T1700Z-rug.json").unlink()
    (work / "site" / "media" / "rug.jpg").write_bytes(b"other")
    with pytest.raises(mqe.Refused, match="different content"):
        run_make(work, write=True)
    assert (work / "site" / "media" / "rug.jpg").read_bytes() == b"other"


def test_same_media_already_in_pages_is_reused(work):
    (work / "site" / "media").mkdir(parents=True)
    (work / "site" / "media" / "rug.jpg").write_bytes((work / "src" / "rug.jpg").read_bytes())
    run_make(work, write=True)
    assert (work / "q" / "2026-11-02T1700Z-rug.json").is_file()


def test_failed_write_cleans_up_what_it_made(work, monkeypatch):
    def boom(self, *a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(Path, "write_text", boom)
    with pytest.raises(OSError):
        run_make(work, write=True)
    assert not (work / "site" / "media" / "rug.jpg").exists()


def test_warnings_do_not_block(work, capsys):
    (work / "src" / "cap.txt").write_text("x" * 2300, encoding="utf-8")
    run_make(work, hashtags=" ".join(f"t{i}" for i in range(31)), alt_text="a" * 1001)
    out = capsys.readouterr().out
    assert out.count("WARN") == 3


def test_png_is_a_warning(work, capsys):
    (work / "src" / "rug.png").write_bytes(b"x")
    run_make(work, media=str(work / "src" / "rug.png"))
    assert "PNG" in capsys.readouterr().out


# ---- fetch (mocked at urlopen so status/header handling is exercised) -------

class FakeResp:
    def __init__(self, status, ctype):
        self.status, self.headers = status, {"Content-Type": ctype}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_reads_status_and_type(monkeypatch):
    monkeypatch.setattr(mqe.urllib.request, "urlopen", lambda req, timeout: FakeResp(200, "image/jpeg"))
    assert mqe.fetch("https://x.invalid/a.jpg") == (200, "image/jpeg")


def test_fetch_http_error_gives_status(monkeypatch):
    def raise_404(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 404, "nf", {"Content-Type": "text/html"}, io.BytesIO(b""))
    monkeypatch.setattr(mqe.urllib.request, "urlopen", raise_404)
    assert mqe.fetch("https://x.invalid/a.jpg") == (404, "text/html")


def test_fetch_network_trouble_is_not_live(monkeypatch):
    def down(req, timeout):
        raise urllib.error.URLError("dns")
    monkeypatch.setattr(mqe.urllib.request, "urlopen", down)
    assert mqe.fetch("https://x.invalid/a.jpg") == (None, None)


def test_fetch_without_mock_hits_the_guard():
    with pytest.raises(AssertionError, match="real network"):
        mqe.fetch("https://x.invalid/a.jpg")


# ---- polling and the timing rule --------------------------------------------

class Clock:
    def __init__(self):
        self.t = NOW

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += timedelta(seconds=s)


def scripted(*answers):
    seq = list(answers)
    urls = []

    def fn(url):
        urls.append(url)
        return seq.pop(0) if len(seq) > 1 else seq[0]
    fn.urls = urls
    return fn


def test_poll_404_then_200_returns_confirm_time():
    clock = Clock()
    got = mqe.wait_for_pages("u", "image/jpeg", fetch_fn=scripted((404, "text/html"), (404, "text/html"), (200, "image/jpeg")),
                             clock=clock, sleep=clock.sleep)
    assert got == NOW + timedelta(seconds=2 * mqe.POLL_EVERY)


def test_poll_wrong_type_keeps_polling_and_charset_parses():
    clock = Clock()
    got = mqe.wait_for_pages("u", "image/jpeg", clock=clock, sleep=clock.sleep,
                             fetch_fn=scripted((200, "text/html"), (200, "Image/JPEG; charset=binary")))
    assert got == NOW + timedelta(seconds=mqe.POLL_EVERY)


def test_poll_gives_up_at_cap():
    clock = Clock()
    with pytest.raises(mqe.Refused, match="gave up after 30 minutes"):
        mqe.wait_for_pages("u", "image/jpeg", fetch_fn=scripted((404, None)), clock=clock, sleep=clock.sleep)
    assert clock.t - NOW >= mqe.POLL_MAX


def test_url_built_like_publisher(monkeypatch):
    monkeypatch.setenv("PAGES_BASE", "https://o.github.io/r")
    url = mqe.media_url("https://o.github.io/r", "media/my rug.jpg")
    assert url == "https://o.github.io/r/media/my%20rug.jpg"


def confirm_args(entry, **over):
    a = ["confirm", str(entry), "--pages-base", "https://o.github.io/r/"]
    for k, v in over.items():
        a += ["--" + k.replace("_", "-"), str(v)]
    return mqe.parser().parse_args(a)


def entry_file(work, due):
    p = work / "e.json"
    p.write_text(json.dumps({"publish_at": due, "type": "IMAGE", "file": "media/rug.jpg"}), encoding="utf-8")
    return p


def run_confirm(work, due, fetch_fn, **over):
    clock = Clock()
    mqe.confirm(confirm_args(entry_file(work, due), **over), clock=clock, fetch_fn=fetch_fn, sleep=clock.sleep)
    return clock


def test_confirm_ok_when_due_is_after_margin(work, capsys):
    run_confirm(work, "2026-11-01T12:15:00Z", scripted((200, "image/jpeg")))
    assert "OK: due" in capsys.readouterr().out


def test_confirm_refuses_due_inside_margin(work):
    with pytest.raises(mqe.Refused, match="sooner than the confirmed fetch plus the margin"):
        run_confirm(work, "2026-11-01T12:14:59Z", scripted((200, "image/jpeg")))


def test_confirm_margin_counts_from_confirmed_fetch_not_start(work):
    fn = scripted((404, None), (404, None), (200, "image/jpeg"))  # confirmed 40 s after start
    with pytest.raises(mqe.Refused, match="sooner"):
        run_confirm(work, "2026-11-01T12:15:00Z", fn)


def test_confirm_refuses_already_due_entry(work):
    with pytest.raises(mqe.Refused, match="sooner"):
        run_confirm(work, "2026-10-01T00:00:00Z", scripted((200, "image/jpeg")))


def test_margin_can_be_raised_not_lowered(work):
    with pytest.raises(mqe.Refused, match="sooner"):
        run_confirm(work, "2026-11-01T12:20:00Z", scripted((200, "image/jpeg")), extra_margin_minutes=10)
    with pytest.raises(mqe.Refused, match="sooner"):  # a negative extra changes nothing
        run_confirm(work, "2026-11-01T12:14:00Z", scripted((200, "image/jpeg")), extra_margin_minutes=-60)
    assert mqe.CONFIRM_MARGIN > mqe.CACHE_404


def test_confirm_checks_the_type_for_the_file(work):
    p = work / "e.json"
    p.write_text(json.dumps({"publish_at": LATER, "type": "REELS", "file": "media/r.mp4"}), encoding="utf-8")
    clock = Clock()
    fn = scripted((200, "image/jpeg"))  # never the right type for an .mp4
    with pytest.raises(mqe.Refused, match="gave up"):
        mqe.confirm(confirm_args(p, max_wait_minutes=1), clock=clock, fetch_fn=fn, sleep=clock.sleep)
    assert fn.urls[0] == "https://o.github.io/r/media/r.mp4"


def test_confirm_requires_https(work):
    a = confirm_args(entry_file(work, LATER))
    a.pages_base = "http://o.github.io/r/"
    with pytest.raises(mqe.Refused, match="https"):
        mqe.confirm(a, fetch_fn=scripted((200, "image/jpeg")))


# ---- command line ------------------------------------------------------------

def test_main_returns_2_on_refusal(work, capsys):
    code = mqe.main(["make", "--media", str(work / "src" / "rug.jpg"), "--caption-file", str(work / "src" / "cap.txt"),
                     "--publish-at", LATER, "--approved", " "])
    assert code == 2 and "REFUSED" in capsys.readouterr().err


def test_runs_as_a_script_dry_by_default(work):
    """A real subprocess: sys.path[0] is tools/, so the tool must find publish.py itself."""
    far = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    before = snapshot(work)
    r = subprocess.run([sys.executable, str(TOOL), "make", "--media", str(work / "src" / "rug.jpg"),
                        "--caption-file", str(work / "src" / "cap.txt"), "--publish-at", far, "--approved", WORDS,
                        "--pages-dir", str(work / "site"), "--queue-dir", str(work / "q")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "DRY RUN: nothing written" in r.stdout
    assert snapshot(work) == before
