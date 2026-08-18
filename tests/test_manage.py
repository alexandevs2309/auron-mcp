"""Operator CLI tests (auron-manage login/status/clean)."""

from types import SimpleNamespace

import pytest

from auron_mcp import manage

API_BASE_URL = "http://localhost:8000"


class FakeResponse:
    status_code = 200

    def json(self):
        return {"user": {"email": "a@b.test", "role": "CAJERA"}, "tenant": {"subdomain": "salon-uno"}}


class FakeClient:
    def __init__(self, *args, **kwargs):
        self.cookies = SimpleNamespace(jar=[])

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def post(self, path, json=None):
        self.cookies.jar = [
            SimpleNamespace(name="access_token", value="abc", domain="localhost", path="/"),
            SimpleNamespace(name="refresh_token", value="rt", domain="localhost", path="/"),
        ]
        return FakeResponse()


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setattr(manage, "settings", SimpleNamespace(api_base_url=API_BASE_URL))
    return manage.settings


def make_args(**overrides):
    args = SimpleNamespace(email="a@b.test", password="pw", subdomain="salon-uno")
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def test_parser_has_login_status_clean_subcommands():
    sub = [a for a in manage.build_parser()._subparsers._group_actions[0].choices]
    assert set(sub) == {"login", "status", "clean"}


def test_login_persists_cookies(tmp_path, monkeypatch, settings):
    monkeypatch.setattr(manage, "_session_path", lambda: tmp_path / "session.json")
    monkeypatch.setattr(manage.httpx, "Client", FakeClient)

    code = manage.cmd_login(make_args())
    assert code == 0

    saved = (tmp_path / "session.json").read_text(encoding="utf-8")
    assert "access_token" in saved
    assert "abc" in saved


def test_status_without_session_returns_error(tmp_path, monkeypatch, settings):
    monkeypatch.setattr(manage, "_session_path", lambda: tmp_path / "missing.json")
    assert manage.cmd_status(make_args()) == 1


def test_clean_removes_persisted_session(tmp_path, monkeypatch):
    session_path = tmp_path / "session.json"
    session_path.write_text('{"cookies": []}', encoding="utf-8")
    monkeypatch.setattr(manage, "_session_path", lambda: session_path)

    assert manage.cmd_clean(make_args()) == 0
    assert not session_path.exists()


def test_login_without_base_url_fails(monkeypatch):
    monkeypatch.setattr(manage, "settings", SimpleNamespace(api_base_url=""))
    assert manage.cmd_login(make_args()) == 2
