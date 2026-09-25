import pytest

from spotify_to_ytmusic.web.auth import headers_from_paste

CURL = """curl 'https://music.youtube.com/youtubei/v1/browse?prettyPrint=false' \\
  -H 'accept: */*' \\
  -H 'Authorization: SAPISIDHASH 123_abc' \\
  -H 'x-goog-authuser: 0' \\
  -b 'VISITOR_INFO1_LIVE=v; SAPISID=secret/xyz; __Secure-3PAPISID=secret/xyz' \\
  --data-raw '{"context":{}}'"""


def test_parses_chrome_curl_with_cookie_flag():
    headers = headers_from_paste(CURL)
    assert headers["cookie"].startswith("VISITOR_INFO1_LIVE=v; SAPISID=secret/xyz")
    assert headers["x-goog-authuser"] == "0"
    assert headers["authorization"] == "SAPISIDHASH 123_abc"


def test_parses_cookie_passed_as_header():
    text = "curl 'https://music.youtube.com/' -H 'cookie: SAPISID=a; b=c' -H 'x-goog-authuser: 1'"
    assert headers_from_paste(text)["cookie"] == "SAPISID=a; b=c"


def test_accepts_raw_header_block():
    text = "accept: */*\ncookie: SAPISID=a; b=c\nx-goog-authuser: 0\n"
    headers = headers_from_paste(text)
    assert headers["cookie"] == "SAPISID=a; b=c"


def test_rejects_paste_without_login_cookie():
    with pytest.raises(ValueError, match="SAPISID"):
        headers_from_paste(
            "curl 'https://music.youtube.com/' -b 'VISITOR_INFO1_LIVE=v'"
        )


def test_rejects_garbage():
    with pytest.raises(ValueError):
        headers_from_paste("hello world")


def test_credentials_dir_is_owner_only(tmp_path, monkeypatch):
    from spotify_to_ytmusic.web import auth

    web_dir = tmp_path / "web"
    monkeypatch.setattr(auth, "WEB_DIR", web_dir)
    monkeypatch.setattr(auth, "CONFIG_FILE", web_dir / "config.json")
    auth.save_config({"spotify_client_id": "x"})
    assert web_dir.stat().st_mode & 0o777 == 0o700
