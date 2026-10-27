import argparse

import pytest
from fastapi.testclient import TestClient

from lfs_tracker.__main__ import build_auth
from lfs_tracker.auth import MAX_FAILURES, AuthConfig, LoginLimiter, load_session_secret
from lfs_tracker.events import EventManager
from lfs_tracker.settings import Settings
from lfs_tracker.tracker import Tracker
from lfs_tracker.web import create_app
from tests.test_tracker import FakeLink

CONFIG = AuthConfig(username="admin", password="correct horse", session_secret="test-secret")


def make_client(tmp_path, config=CONFIG, **kwargs):
    app = create_app(Tracker(), EventManager(), Settings(tmp_path / "s.json"), FakeLink(), config)
    return TestClient(app, follow_redirects=False, **kwargs)


def sign_in(client, username="admin", password="correct horse"):
    return client.post("/login", data={"username": username, "password": password})


def test_nothing_is_reachable_without_signing_in(tmp_path):
    client = make_client(tmp_path)
    assert client.get("/api/settings").status_code == 401
    assert client.post("/api/qualify").status_code == 401
    assert client.post("/api/start-race").status_code == 401
    assert client.get("/api/me").status_code == 401
    page = client.get("/")
    assert (page.status_code, page.headers["location"]) == (303, "/login")
    form = client.get("/login")
    assert form.status_code == 200 and "type=password" in form.text


def test_correct_login_gets_in_and_can_sign_out(tmp_path):
    client = make_client(tmp_path)
    reply = sign_in(client)
    assert (reply.status_code, reply.headers["location"]) == (303, "/")
    assert client.get("/api/settings").status_code == 200
    assert client.get("/api/me").json() == {"user": "admin"}
    assert client.get("/").status_code == 200
    assert client.get("/login").headers["location"] == "/"  # already signed in

    assert client.post("/logout").json() == {"signed_out": True}
    assert client.get("/api/settings").status_code == 401


@pytest.mark.parametrize(
    "username,password",
    [("admin", "wrong"), ("someone", "correct horse"), ("", ""), ("admin", "correct horse ")],
)
def test_wrong_credentials_are_refused(tmp_path, username, password):
    client = make_client(tmp_path)
    reply = sign_in(client, username, password)
    assert reply.status_code == 401 and "Wrong username or password" in reply.text
    assert client.get("/api/settings").status_code == 401


def test_repeated_failures_lock_the_address_out_even_for_the_right_password(tmp_path):
    client = make_client(tmp_path)
    for _ in range(MAX_FAILURES):
        assert sign_in(client, password="nope").status_code == 401
    assert sign_in(client).status_code == 429
    assert client.get("/api/settings").status_code == 401


def test_limiter_unlocks_after_the_lockout_and_forgets_after_success():
    now = [0.0]
    limiter = LoginLimiter(clock=lambda: now[0])
    for _ in range(MAX_FAILURES):
        limiter.failed("a")
    assert limiter.locked("a") and not limiter.locked("b")
    now[0] += 10_000
    assert not limiter.locked("a")
    limiter.failed("a")
    limiter.succeeded("a")
    for _ in range(MAX_FAILURES - 1):
        limiter.failed("a")
    assert not limiter.locked("a")


def test_changing_the_password_ends_existing_sessions(tmp_path):
    client = make_client(tmp_path)
    sign_in(client)
    assert client.get("/api/settings").status_code == 200
    changed = AuthConfig(username="admin", password="a new password", session_secret="test-secret")
    app = create_app(Tracker(), EventManager(), Settings(tmp_path / "s2.json"), FakeLink(), changed)
    other = TestClient(app, follow_redirects=False)
    other.cookies.update(client.cookies)  # the old cookie, same signing secret
    assert other.get("/api/settings").status_code == 401


def test_cookie_is_secure_only_when_served_over_https(tmp_path):
    secure = AuthConfig("admin", "correct horse", "s", secure_cookies=True)
    reply = sign_in(make_client(tmp_path, secure, base_url="https://testserver"))
    assert "secure" in reply.headers["set-cookie"].lower()
    assert "secure" not in sign_in(make_client(tmp_path)).headers["set-cookie"].lower()
    assert "httponly" in sign_in(make_client(tmp_path)).headers["set-cookie"].lower()


def test_public_results_need_no_login_but_nothing_else_does(tmp_path):
    client = make_client(tmp_path)
    assert client.get("/results").status_code == 200
    assert client.get("/results/", follow_redirects=True).status_code == 200
    assert client.get("/public/api/events").json() == {"mode": 0, "events": []}
    assert client.get("/public/api/events/1").status_code == 404
    # read-only: nothing can be changed, and the private API and its lookalikes stay locked
    assert client.post("/public/api/events").status_code == 405
    assert client.post("/public/api/events/end").status_code in (404, 405)
    for path in ("/api/events", "/api/players", "/api/settings", "/publicity", "/results.json", "/public"):
        assert client.get(path).status_code in (303, 401), path


def test_without_auth_the_page_is_open_and_me_is_empty(tmp_path):
    client = TestClient(create_app(Tracker(), EventManager(), Settings(tmp_path / "s.json"), FakeLink()))
    assert client.get("/api/settings").status_code == 200
    assert client.get("/api/me").json() == {"user": None}


def args(**values):
    base = dict(web_host="127.0.0.1", web_username="", web_password="", public_url="")
    return argparse.Namespace(**{**base, **values})


def test_build_auth_requires_a_login_to_serve_the_network(tmp_path):
    assert build_auth(args(), tmp_path) is None  # this computer only
    with pytest.raises(SystemExit):
        build_auth(args(web_host="0.0.0.0"), tmp_path)
    with pytest.raises(SystemExit):  # half configured
        build_auth(args(web_username="admin"), tmp_path)
    with pytest.raises(SystemExit):  # too short
        build_auth(args(web_username="admin", web_password="short"), tmp_path)

    config = build_auth(
        args(web_username="admin", web_password="long enough", web_host="0.0.0.0", public_url="https://t.example.com"),
        tmp_path,
    )
    assert (config.username, config.password, config.secure_cookies) == ("admin", "long enough", True)


def test_session_secret_is_kept_between_runs(tmp_path, monkeypatch):
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    first = load_session_secret(tmp_path / "secret")
    assert first and load_session_secret(tmp_path / "secret") == first
    monkeypatch.setenv("SESSION_SECRET", "from-env")
    assert load_session_secret(tmp_path / "secret") == "from-env"
