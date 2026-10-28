"""Shared fixtures. Every test gets a throwaway repo layout and a network that raises if touched."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import publish  # noqa: E402


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Instagram must never be reached from a test: any real HTTP attempt fails loudly."""
    def boom(*a, **k):
        raise AssertionError("test tried to make a real network call")
    monkeypatch.setattr(publish.urllib.request, "urlopen", boom)
    monkeypatch.setenv("IG_TOKEN", "not-a-real-token")  # dummy value; the real one lives only in Actions
    monkeypatch.setenv("PAGES_BASE", "https://example.invalid/pages/")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Empty queue/, done/, media/ under tmp_path; publish.ROOT points there, never at the real folders."""
    for d in ("queue", "done", "media"):
        (tmp_path / d).mkdir()
    monkeypatch.setattr(publish, "ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def fake_api(monkeypatch):
    """Replace publish.call with a recorder. Set fake_api.live to the list returned for me/media."""
    class Api:
        live = []
        calls = []

        def __call__(self, method, path, **params):
            self.calls.append((method, path, params))
            if (method, path) == ("GET", "me/media"):
                return {"data": self.live}
            if (method, path) == ("POST", "me/media"):
                return {"id": "container1"}
            if method == "GET" and path == "container1":
                return {"status_code": "FINISHED"}
            if (method, path) == ("POST", "me/media_publish"):
                return {"id": "media1"}
            if method == "GET" and path == "media1":
                return {"is_ai_generated": True, "permalink": "https://instagram.invalid/p/x/"}
            raise AssertionError(f"unexpected API call {method} {path}")

        def posts(self):
            return [c for c in self.calls if c[0] == "POST"]

    api = Api()
    api.calls = []
    monkeypatch.setattr(publish, "call", api)
    monkeypatch.setattr(publish.time, "sleep", lambda s: None)
    return api
