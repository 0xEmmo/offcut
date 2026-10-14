"""Upload clips to YouTube Shorts via Data API v3. Graceful without credentials."""
from __future__ import annotations

import json, threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLIENT_SECRET = ROOT / "client_secret.json"
TOKEN = ROOT / "data" / "youtube_token.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

_lock = threading.Lock()
_oauth_status = {"state": "idle", "error": None, "message": None}


def _import_google():
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        return Credentials, InstalledAppFlow, Request, build, MediaFileUpload
    except ImportError as e:
        raise RuntimeError(
            "google-api-python-client / google-auth-oauthlib not installed. "
            "Run: pip install google-api-python-client google-auth-oauthlib google-auth-httplib2"
        ) from e


def has_client_secret() -> bool:
    return CLIENT_SECRET.exists()


def connected() -> bool:
    if not TOKEN.exists():
        return False
    try:
        data = json.loads(TOKEN.read_text())
        return bool(data.get("refresh_token") or data.get("token"))
    except Exception:
        return False


def status() -> dict:
    return {
        "connected": connected(),
        "has_client_secret": has_client_secret(),
        "oauth": dict(_oauth_status),
        "client_secret_path": str(CLIENT_SECRET),
        "token_path": str(TOKEN),
    }


def _creds():
    Credentials, InstalledAppFlow, Request, build, MediaFileUpload = _import_google()
    if not TOKEN.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN.parent.mkdir(parents=True, exist_ok=True)
        TOKEN.write_text(creds.to_json())
    return creds if creds and creds.valid else None


def start_oauth() -> dict:
    """Kick off installed-app OAuth in a background thread (opens a local browser)."""
    if not has_client_secret():
        raise FileNotFoundError(
            f"Put your Google OAuth Desktop credentials at {CLIENT_SECRET.name} "
            "(see README → Connect YouTube)."
        )
    with _lock:
        if _oauth_status["state"] == "running":
            return {"ok": True, "message": "OAuth already in progress — check your browser"}
        _oauth_status.update(state="running", error=None, message="Waiting for Google sign-in…")

    def run():
        try:
            Credentials, InstalledAppFlow, Request, build, MediaFileUpload = _import_google()
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
            creds = flow.run_local_server(port=0, open_browser=True,
                                         prompt="consent", access_type="offline")
            TOKEN.parent.mkdir(parents=True, exist_ok=True)
            TOKEN.write_text(creds.to_json())
            _oauth_status.update(state="done", error=None, message="YouTube connected")
        except Exception as e:
            _oauth_status.update(state="error", error=str(e), message=None)

    threading.Thread(target=run, daemon=True, name="offcut-yt-oauth").start()
    return {"ok": True, "message": "Browser should open for Google sign-in"}


def disconnect():
    if TOKEN.exists():
        TOKEN.unlink()
    _oauth_status.update(state="idle", error=None, message=None)


def upload_clip(mp4: Path, clip: dict, privacy: str = "private") -> dict:
    """Upload one Short. privacy: private|unlisted|public. Default private for review."""
    Credentials, InstalledAppFlow, Request, build, MediaFileUpload = _import_google()
    if not mp4.exists():
        raise FileNotFoundError(mp4)
    creds = _creds()
    if not creds:
        raise RuntimeError("YouTube not connected. Click Connect YouTube first.")
    title = (clip.get("title") or mp4.stem).strip()
    if "#Shorts" not in title and "#shorts" not in title.lower():
        title = f"{title} #Shorts"
    title = title[:100]
    desc = (clip.get("description") or "").strip()
    tags = []
    for t in clip.get("hashtags") or []:
        tags.append(str(t).lstrip("#"))
    if "Shorts" not in tags:
        tags.append("Shorts")
    body = {
        "snippet": {
            "title": title,
            "description": desc,
            "tags": tags[:15],
            "categoryId": "22",
        },
        "status": {
            "privacyStatus": privacy if privacy in ("private", "unlisted", "public") else "private",
            "selfDeclaredMadeForKids": False,
        },
    }
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    media = MediaFileUpload(str(mp4), mimetype="video/mp4", resumable=True, chunksize=1024 * 1024)
    req = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        status_obj, resp = req.next_chunk()
    vid = resp["id"]
    return {
        "id": vid,
        "url": f"https://www.youtube.com/shorts/{vid}",
        "watch": f"https://www.youtube.com/watch?v={vid}",
        "privacy": body["status"]["privacyStatus"],
        "title": title,
    }
