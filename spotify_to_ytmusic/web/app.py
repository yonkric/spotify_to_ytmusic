"""Local web UI: connect both accounts, pick what to move, watch progress."""

import logging
import secrets
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from spotify_to_ytmusic.sync.engine import Selection
from spotify_to_ytmusic.sync.spotify_library import SpotifyLibrary
from spotify_to_ytmusic.sync.ytm_library import YTMusicLibrary
from spotify_to_ytmusic.web import auth
from spotify_to_ytmusic.web.jobs import JobRunner

log = logging.getLogger(__name__)

HOST = "127.0.0.1"
PORT = 8765
ORIGIN = f"http://{HOST}:{PORT}"
SPOTIFY_REDIRECT_URI = f"{ORIGIN}/callback/spotify"
ALLOWED_HOSTS = {f"{HOST}:{PORT}", f"localhost:{PORT}"}

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def home_with_error(message: str) -> RedirectResponse:
    return RedirectResponse(f"/?error={quote(message)}", 303)


class State:
    """Process-wide state; the app serves a single local user."""

    def __init__(self):
        self.jobs = JobRunner()
        self.spotify_auth = None
        self.oauth_state = None

    def spotify_auth_for(self, client_id: str):
        if self.spotify_auth is None or self.spotify_auth.client_id != client_id:
            self.spotify_auth = auth.spotify_auth_manager(
                client_id, SPOTIFY_REDIRECT_URI
            )
        return self.spotify_auth

    def spotify(self) -> SpotifyLibrary | None:
        client_id = auth.load_config().get("spotify_client_id")
        if not client_id:
            return None
        client = auth.spotify_client(self.spotify_auth_for(client_id))
        return SpotifyLibrary(client) if client else None

    def ytm(self) -> YTMusicLibrary | None:
        client = auth.ytm_client()
        return YTMusicLibrary(client) if client else None

    def library(self, name: str):
        lib = {"spotify": self.spotify, "ytm": self.ytm}[name]()
        if lib is None:
            raise HTTPException(400, f"{name} is not connected")
        return lib


def create_app() -> FastAPI:
    app = FastAPI(title="Spotify ⇄ YouTube Music")
    state = State()

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # Block DNS-rebinding and cross-site form posts from other pages in the browser
        if request.headers.get("host") not in ALLOWED_HOSTS:
            return PlainTextResponse("Forbidden host", status_code=403)
        if request.method == "POST" and request.headers.get("origin") not in {
            ORIGIN,
            f"http://localhost:{PORT}",
        }:
            return PlainTextResponse("Forbidden origin", status_code=403)
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        config = auth.load_config()
        spotify, ytm = state.spotify(), state.ytm()
        error = request.query_params.get("error")
        spotify_user = None
        if spotify:
            try:
                spotify_user = (
                    spotify.api.current_user()["display_name"] or spotify.user_id
                )
            except Exception as ex:
                log.exception("Spotify profile lookup failed")
                error = f"Spotify login no longer works ({type(ex).__name__}); please reconnect."
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "client_id": config.get("spotify_client_id"),
                "redirect_uri": SPOTIFY_REDIRECT_URI,
                "spotify_user": spotify_user,
                "ytm_connected": ytm is not None,
                "error": error,
                "job": state.jobs.latest(),
            },
        )

    # ---- Spotify ------------------------------------------------------------

    @app.post("/spotify/client-id")
    def set_client_id(client_id: str = Form(...)):
        client_id = client_id.strip()
        if len(client_id) != 32 or not all(
            c in "0123456789abcdef" for c in client_id.lower()
        ):
            return home_with_error(
                "That doesn't look like a Spotify Client ID (32 hex characters)."
            )
        auth.save_config({**auth.load_config(), "spotify_client_id": client_id})
        auth.forget(auth.SPOTIFY_TOKEN_FILE)
        return RedirectResponse("/", 303)

    @app.get("/spotify/login")
    def spotify_login():
        client_id = auth.load_config().get("spotify_client_id")
        if not client_id:
            return RedirectResponse("/", 303)
        state.oauth_state = secrets.token_urlsafe(16)
        manager = state.spotify_auth_for(client_id)
        manager.get_pkce_handshake_parameters()
        return RedirectResponse(manager.get_authorize_url(state.oauth_state), 303)

    @app.get("/callback/spotify")
    def spotify_callback(
        request: Request, code: str | None = None, error: str | None = None
    ):
        returned_state = request.query_params.get("state")
        if error:
            return home_with_error(f"Spotify login was cancelled ({error}).")
        if (
            not code
            or returned_state != state.oauth_state
            or state.spotify_auth is None
        ):
            return home_with_error(
                "Spotify login expired or didn't match; please try again."
            )
        state.spotify_auth.get_access_token(code, check_cache=False)
        state.oauth_state = None
        return RedirectResponse("/", 303)

    @app.post("/spotify/logout")
    def spotify_logout():
        auth.forget(auth.SPOTIFY_TOKEN_FILE)
        state.jobs.forget_finished()
        return RedirectResponse("/", 303)

    # ---- YouTube Music ------------------------------------------------------

    @app.post("/ytm/connect")
    def ytm_connect(headers: str = Form(...)):
        try:
            auth.save_ytm_auth(headers)
        except ValueError as ex:
            return home_with_error(str(ex))
        except Exception as ex:
            log.exception("YouTube Music auth check failed")
            return home_with_error(
                f"YouTube Music rejected those headers ({type(ex).__name__}: {ex})."
            )
        return RedirectResponse("/", 303)

    @app.post("/ytm/logout")
    def ytm_logout():
        auth.forget(auth.YTM_AUTH_FILE)
        state.jobs.forget_finished()
        return RedirectResponse("/", 303)

    # ---- transfer -----------------------------------------------------------

    @app.get("/library", response_class=HTMLResponse)
    def library(request: Request, source: str):
        if source not in ("spotify", "ytm"):
            raise HTTPException(400, "source must be spotify or ytm")
        lib = state.library(source)
        try:
            playlists = sorted(lib.list_playlists(), key=lambda p: p["name"].lower())
        except Exception as ex:
            log.exception("Listing %s playlists failed", lib.name)
            return templates.TemplateResponse(
                request,
                "_error.html",
                {
                    "message": f"Couldn't read your {lib.name} library: {auth.friendly_error(ex)}"
                },
            )
        return templates.TemplateResponse(
            request,
            "_library.html",
            {"source": source, "source_name": lib.name, "playlists": playlists},
        )

    @app.post("/transfer", response_class=HTMLResponse)
    def transfer(
        request: Request,
        source: str = Form(...),
        playlist_ids: list[str] = Form(default=[]),
        liked: bool = Form(False),
        albums: bool = Form(False),
        artists: bool = Form(False),
    ):
        if source not in ("spotify", "ytm"):
            raise HTTPException(400, "source must be spotify or ytm")
        selection = Selection(
            playlist_ids=playlist_ids, liked=liked, albums=albums, artists=artists
        )
        if not (selection.playlist_ids or liked or albums or artists):
            return templates.TemplateResponse(
                request, "_error.html", {"message": "Pick at least one thing to move."}
            )
        src = state.library(source)
        dest = state.library("ytm" if source == "spotify" else "spotify")
        try:
            job = state.jobs.start(src, dest, selection)
        except RuntimeError as ex:
            return templates.TemplateResponse(
                request, "_error.html", {"message": str(ex)}
            )
        return templates.TemplateResponse(request, "_job.html", {"job": job})

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_status(request: Request, job_id: str):
        job = state.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "No such job")
        return templates.TemplateResponse(request, "_job.html", {"job": job})

    @app.post("/jobs/{job_id}/resolve", response_class=HTMLResponse)
    async def job_resolve(request: Request, job_id: str):
        job = state.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "No such job")
        picks: dict[tuple[int, int], str] = {}
        links: dict[tuple[int, int], str] = {}
        for name, value in (await request.form()).multi_items():
            # "pick-<section>-<item>" (empty = none of these), "link-<section>-<item>"
            parts = name.split("-")
            if (
                len(parts) != 3
                or parts[0] not in ("pick", "link")
                or not str(value).strip()
            ):
                continue
            try:
                key = (int(parts[1]), int(parts[2]))
            except ValueError:
                raise HTTPException(400, f"Bad field {name!r}") from None
            (picks if parts[0] == "pick" else links)[key] = str(value).strip()

        def ids_from_links() -> tuple[dict, list[str]]:
            found, problems = {}, []
            for (section, item), link in links.items():
                if not (
                    0 <= section < len(job.report)
                    and 0 <= item < len(job.report[section]["not_found"])
                ):
                    raise HTTPException(400, f"No unmatched item {section}-{item}")
                label = job.report[section]["not_found"][item]["label"]
                try:
                    found[(section, item)] = job.dest.id_from_link(
                        job.report[section]["kind"], link
                    )
                except ValueError as ex:
                    problems.append(f"{label}: {ex}")
            return found, problems

        pasted_ids, problems = await run_in_threadpool(ids_from_links)
        choices: dict[int, dict[int, str]] = {}
        pasted: dict[int, set[int]] = {}
        for (section, item), chosen in {**picks, **pasted_ids}.items():  # a link wins
            choices.setdefault(section, {})[item] = chosen
        for section, item in pasted_ids:
            pasted.setdefault(section, set()).add(item)

        notice = None
        if choices:
            try:
                added = await run_in_threadpool(
                    state.jobs.resolve, job, choices, pasted
                )
                notice = (
                    f"Added {added} of your picks and remembered them for next time."
                )
            except ValueError as ex:
                raise HTTPException(400, str(ex)) from None
            except Exception as ex:
                log.exception("Applying picks for job %s failed", job_id)
                problems.append(f"Couldn't add your picks: {auth.friendly_error(ex)}")
        return templates.TemplateResponse(
            request, "_job.html", {"job": job, "notice": notice, "problems": problems}
        )

    @app.get("/jobs/{job_id}/not-found.txt", response_class=PlainTextResponse)
    def job_not_found(job_id: str):
        job = state.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "No such job")
        return PlainTextResponse(
            job.not_found_text,
            headers={
                "Content-Disposition": f'attachment; filename="not-found-{job_id}.txt"'
            },
        )

    return app


def serve() -> None:
    import uvicorn

    print(f"Open {ORIGIN} in your browser")
    uvicorn.run(create_app(), host=HOST, port=PORT)
