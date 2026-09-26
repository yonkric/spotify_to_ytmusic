"""Credentials for the web UI: Spotify PKCE login and YouTube Music browser headers."""

import json
import shlex
from itertools import pairwise
from pathlib import Path

import spotipy
from spotipy import CacheFileHandler
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyPKCE
from ytmusicapi import YTMusic, setup

from spotify_to_ytmusic.settings import CACHE_DIR
from spotify_to_ytmusic.sync.spotify_library import SCOPES

WEB_DIR = CACHE_DIR / "web"
CONFIG_FILE = WEB_DIR / "config.json"
SPOTIFY_TOKEN_FILE = WEB_DIR / "spotify_token.json"
YTM_AUTH_FILE = WEB_DIR / "ytm_browser.json"
MATCH_CACHE_FILE = WEB_DIR / "matches.json"
REVIEW_FILE = WEB_DIR / "review.json"  # latest finished transfer, kept until the next


def _ensure_private_dir() -> None:
    """Credentials live here (Google cookies, Spotify tokens): owner-only access."""
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    WEB_DIR.chmod(0o700)


# ---- config -----------------------------------------------------------------


def load_config() -> dict:
    if not CONFIG_FILE.is_file():
        return {}
    return json.loads(CONFIG_FILE.read_text())


def save_config(config: dict) -> None:
    _ensure_private_dir()
    CONFIG_FILE.write_text(json.dumps(config, indent=2))


# ---- Spotify ----------------------------------------------------------------


def spotify_auth_manager(client_id: str, redirect_uri: str) -> SpotifyPKCE:
    _ensure_private_dir()
    return SpotifyPKCE(
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=SCOPES,
        cache_handler=CacheFileHandler(cache_path=str(SPOTIFY_TOKEN_FILE)),
        open_browser=False,
    )


def spotify_client(auth: SpotifyPKCE) -> spotipy.Spotify | None:
    """A client only when a valid cached token exists; spotipy would otherwise
    fall back to an interactive terminal prompt and hang the server."""
    if auth.validate_token(auth.cache_handler.get_cached_token()) is None:
        return None
    # no automatic retry on 429: Spotify's Retry-After can be ~20 hours for
    # development-mode apps, which would silently freeze a transfer
    return spotipy.Spotify(
        auth_manager=auth, retries=3, status_forcelist=(500, 502, 503, 504)
    )


def friendly_error(ex: Exception) -> str:
    """A message the user can act on, keeping details for anything unexpected."""
    if isinstance(ex, SpotifyException) and ex.http_status == 429:
        retry_after = int((ex.headers or {}).get("Retry-After", 0))
        wait = (
            f"about {round(retry_after / 3600)} hours"
            if retry_after >= 3600
            else f"about {max(1, round(retry_after / 60))} minutes"
        )
        return (
            f"Spotify is rate-limiting this app (too many requests today). Try again in "
            f"{wait}; everything already copied is kept."
        )
    return f"{type(ex).__name__}: {ex}"


# ---- YouTube Music ----------------------------------------------------------


def _headers_from_curl(text: str) -> dict:
    tokens = shlex.split(text.replace("\\\n", " "))
    headers = {}
    for flag, value in pairwise(tokens):
        if flag in ("-H", "--header"):
            name, _, val = value.partition(":")
            headers[name.strip().lower()] = val.strip()
        elif flag in ("-b", "--cookie"):
            headers["cookie"] = value
    return headers


def _headers_from_raw(text: str) -> dict:
    headers = {}
    for line in text.splitlines():
        name, sep, val = line.partition(":")
        if sep and name.strip() and " " not in name.strip():
            headers[name.strip().lower()] = val.strip()
    return headers


def headers_from_paste(text: str) -> dict:
    """Parse Chrome's "Copy as cURL" output or a raw request-header block."""
    text = text.strip()
    headers = (
        _headers_from_curl(text)
        if text.startswith("curl ")
        else _headers_from_raw(text)
    )
    if "SAPISID" not in headers.get("cookie", ""):
        raise ValueError(
            "No SAPISID login cookie found in the pasted request. Copy a music.youtube.com "
            "'browse' request while signed in (right-click → Copy → Copy as cURL)."
        )
    return headers


def save_ytm_auth(text: str) -> YTMusic:
    """Store browser auth from a pasted request and verify it with a read-only call."""
    headers = headers_from_paste(text)
    _ensure_private_dir()
    raw = "\n".join(f"{name}: {value}" for name, value in headers.items())
    setup(filepath=str(YTM_AUTH_FILE), headers_raw=raw)
    api = YTMusic(str(YTM_AUTH_FILE))
    try:
        api.get_library_playlists(limit=1)
    except Exception:
        YTM_AUTH_FILE.unlink(missing_ok=True)
        raise
    return api


def ytm_client() -> YTMusic | None:
    return YTMusic(str(YTM_AUTH_FILE)) if YTM_AUTH_FILE.is_file() else None


def forget(path: Path) -> None:
    path.unlink(missing_ok=True)
