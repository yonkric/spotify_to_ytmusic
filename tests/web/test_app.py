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
    dest.suggest = {"Gone": ["g1", "g2"]}
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


def _finished_job(client):
    page = client.post(
        "/transfer", data={"source": "spotify", "playlist_ids": "p1"}, headers=ORIGIN
    ).text
    job_id = page.split('data-job-id="')[1].split('"')[0]
    for _ in range(50):
        page = client.get(f"/jobs/{job_id}").text
        if "Done." in page:
            return job_id, page
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_not_found_shows_reason_and_suggestions_with_listen_links(client, connected):
    _, page = _finished_job(client)
    assert "closest: ..." in page
    assert 'name="pick-0-0" value="g1"' in page
    assert "https://listen.example/track/g2" in page
    assert "None of these" in page


def test_picking_a_suggestion_adds_it(client, connected):
    _, dest = connected
    job_id, _ = _finished_job(client)
    page = client.post(
        f"/jobs/{job_id}/resolve", data={"pick-0-0": "g2"}, headers=ORIGIN
    ).text
    assert "Added 1 of your picks" in page
    assert dest.added["new-Road trip"] == ["d1", "g2"]
    assert "not found — review" not in page  # nothing left to review
    assert "g2" in (auth.MATCH_CACHE_FILE.read_text())


def test_none_of_these_changes_nothing(client, connected):
    _, dest = connected
    job_id, _ = _finished_job(client)
    page = client.post(
        f"/jobs/{job_id}/resolve", data={"pick-0-0": ""}, headers=ORIGIN
    ).text
    assert dest.added["new-Road trip"] == ["d1"]
    assert 'value="g1"' in page


def test_forged_pick_is_rejected(client, connected):
    _, dest = connected
    job_id, _ = _finished_job(client)
    response = client.post(
        f"/jobs/{job_id}/resolve", data={"pick-0-0": "not-offered"}, headers=ORIGIN
    )
    assert response.status_code == 400
    assert dest.added["new-Road trip"] == ["d1"]


def test_switching_accounts_clears_old_review_lists(client, connected, monkeypatch):
    job_id, _ = _finished_job(client)
    client.post("/ytm/logout", headers=ORIGIN)
    assert client.get(f"/jobs/{job_id}").status_code == 404
    response = client.post(
        f"/jobs/{job_id}/resolve", data={"pick-0-0": "g1"}, headers=ORIGIN
    )
    assert response.status_code == 404
