import time

import pytest
from fastapi.testclient import TestClient

from spotify_to_ytmusic.web import app as web_app
from spotify_to_ytmusic.web import auth, jobs
from tests.sync.test_engine import FakeLibrary, t

ORIGIN = {"origin": web_app.ORIGIN}


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in (
        "CONFIG_FILE",
        "SPOTIFY_TOKEN_FILE",
        "YTM_AUTH_FILE",
        "MATCH_CACHE_FILE",
    ):
        monkeypatch.setattr(auth, name, tmp_path / getattr(auth, name).name)
    monkeypatch.setattr(auth, "WEB_DIR", tmp_path)
    monkeypatch.setattr(jobs, "MATCH_CACHE_FILE", tmp_path / "matches.json")
    return TestClient(web_app.create_app(), base_url=web_app.ORIGIN)


@pytest.fixture
def connected(monkeypatch):
    source = FakeLibrary(playlists={"p1": ("Road trip", [t("One"), t("Gone")])})
    source.name = "Spotify"
    dest = FakeLibrary(known={"One": "d1"})
    dest.name = "YouTube Music"
    monkeypatch.setattr(
        web_app.State, "library", lambda self, n: source if n == "spotify" else dest
    )
    return source, dest


def test_first_visit_asks_for_client_id(client):
    page = client.get("/").text
    assert "Create app" in page
    assert web_app.SPOTIFY_REDIRECT_URI in page


def test_rejects_foreign_host_and_cross_site_posts(client):
    assert client.get("/", headers={"host": "evil.example"}).status_code == 403
    assert (
        client.post(
            "/spotify/logout", headers={"origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert client.post("/spotify/logout").status_code == 403  # no Origin at all


def test_client_id_validation_and_save(client):
    bad = client.post("/spotify/client-id", data={"client_id": "nope"}, headers=ORIGIN)
    assert "Client ID" in bad.text and "error" in str(bad.url)

    client.post(
        "/spotify/client-id",
        data={"client_id": "54d3ad4e7a3148639599d961159317c9"},
        headers=ORIGIN,
    )
    assert auth.load_config()["spotify_client_id"] == "54d3ad4e7a3148639599d961159317c9"
    assert 'href="/spotify/login"' in client.get("/").text


def test_spotify_login_redirects_with_pkce_and_state(client):
    client.post(
        "/spotify/client-id",
        data={"client_id": "54d3ad4e7a3148639599d961159317c9"},
        headers=ORIGIN,
    )
    response = client.get("/spotify/login", follow_redirects=False)
    location = response.headers["location"]
    assert location.startswith("https://accounts.spotify.com/authorize")
    assert "code_challenge=" in location and "state=" in location


def test_callback_with_wrong_state_is_rejected(client):
    page = client.get("/callback/spotify?code=abc&state=forged").text
    assert "didn&#39;t match" in page or "didn't match" in page


def test_ytm_connect_explains_missing_login_cookie(client):
    page = client.post(
        "/ytm/connect", data={"headers": "curl 'x' -b 'a=b'"}, headers=ORIGIN
    ).text
    assert "SAPISID" in page


def test_library_lists_playlists(client, connected):
    page = client.get("/library?source=spotify").text
    assert "Road trip" in page and 'value="p1"' in page


def test_transfer_requires_a_selection(client, connected):
    page = client.post("/transfer", data={"source": "spotify"}, headers=ORIGIN).text
    assert "Pick at least one" in page


def test_transfer_runs_and_reports_not_found(client, connected):
    _, dest = connected
    page = client.post(
        "/transfer", data={"source": "spotify", "playlist_ids": "p1"}, headers=ORIGIN
    ).text
    job_id = page.split('data-job-id="')[1].split('"')[0]

    for _ in range(50):
        page = client.get(f"/jobs/{job_id}").text
        if "Done." in page:
            break
        time.sleep(0.05)

    assert "Done." in page and "Playlist: Road trip" in page
    assert dest.added == {"new-Road trip": ["d1"]}
    report = client.get(f"/jobs/{job_id}/not-found.txt").text
    assert "A - Gone" in report


def test_reloading_the_page_shows_the_latest_job(client, connected, monkeypatch):
    monkeypatch.setattr(web_app.State, "spotify", lambda self: None)
    monkeypatch.setattr(web_app.State, "ytm", lambda self: None)
    assert "data-job-id" not in client.get("/").text

    page = client.post(
        "/transfer", data={"source": "spotify", "playlist_ids": "p1"}, headers=ORIGIN
    ).text
    job_id = page.split('data-job-id="')[1].split('"')[0]

    assert f'data-job-id="{job_id}"' in client.get("/").text
