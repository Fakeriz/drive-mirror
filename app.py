"""Drive Mirror — tempel link apa pun, file-nya masuk Google Drive.

Dua opsi:
- Mode tamu  : tanpa login, file masuk Drive app (service account),
               link dibagikan publik (anyone with link).
- Drive saya : login dengan Google (OAuth), file masuk Drive milik user.
"""
import os
import secrets as pysecrets
import threading
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse

load_dotenv()

from drive_client import ensure_folder, sa_service, share_anyone, upload_file, user_service
from mirror import jobs, run_mirror_job
from oauth import (
    build_auth_url,
    build_user_credentials,
    exchange_code,
    get_userinfo,
    oauth_configured,
)

TOKEN = os.environ.get("MIRROR_TOKEN", "").strip()

app = FastAPI(title="Drive Mirror")

SESSION_SECRET = os.environ.get("SESSION_SECRET", "").strip()
if not SESSION_SECRET:
    SESSION_SECRET = pysecrets.token_hex(32)
    print("PERINGATAN: SESSION_SECRET tidak diisi — session hanya berlaku selama server jalan.")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, same_site="lax")

# sid -> {access_token, refresh_token, expiry, email, name}
user_tokens: dict = {}

GUEST_FOLDER_NAME = "Drive Mirror"


class MirrorRequest(BaseModel):
    url: str
    quality: str = "1080p"
    mode: str = "video"  # video | audio
    target: str = "guest"  # guest | mine


ALLOWED_QUALITIES = {"360p", "480p", "720p", "1080p", "terbaik"}
ALLOWED_MODES = {"video", "audio"}
ALLOWED_TARGETS = {"guest", "mine"}


def check_auth(authorization: str | None):
    if TOKEN:
        if not authorization or authorization != f"Bearer {TOKEN}":
            raise HTTPException(status_code=401, detail="Token salah atau tidak ada.")


def get_login(request: Request) -> dict | None:
    sid = request.session.get("sid")
    return user_tokens.get(sid) if sid else None


@app.get("/api/health")
def health():
    return {"ok": True, "butuh_token": bool(TOKEN), "oauth_ready": oauth_configured()}


@app.get("/api/me")
def me(request: Request):
    tok = get_login(request)
    if not tok:
        return {"logged_in": False, "oauth_ready": oauth_configured()}
    return {
        "logged_in": True,
        "email": tok.get("email"),
        "name": tok.get("name"),
        "oauth_ready": oauth_configured(),
    }


@app.get("/auth/google/login")
def google_login(request: Request):
    if not oauth_configured():
        raise HTTPException(
            status_code=400,
            detail="Login Google belum dikonfigurasi (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / OAUTH_REDIRECT_URI).",
        )
    state = pysecrets.token_urlsafe(16)
    request.session["oauth_state"] = state
    return RedirectResponse(build_auth_url(state))


@app.get("/auth/google/callback")
def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    if error:
        raise HTTPException(status_code=400, detail=f"Login Google dibatalkan: {error}")
    if not code or not state or state != request.session.get("oauth_state"):
        raise HTTPException(status_code=400, detail="State login tidak valid.")
    request.session.pop("oauth_state", None)
    creds = exchange_code(code)
    info = get_userinfo(creds["access_token"])
    sid = pysecrets.token_hex(16)
    user_tokens[sid] = {
        **creds,
        "email": info.get("email"),
        "name": info.get("name"),
    }
    request.session["sid"] = sid
    return RedirectResponse("/?target=mine", status_code=303)


@app.post("/auth/logout")
def logout(request: Request):
    sid = request.session.pop("sid", None)
    if sid:
        user_tokens.pop(sid, None)
    return {"ok": True}


@app.post("/api/mirror")
def mirror(req: MirrorRequest, request: Request, authorization: str | None = Header(None)):
    check_auth(authorization)
    url = (req.url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL harus diawali http:// atau https://")

    quality = (req.quality or "1080p").strip().lower()
    if quality not in ALLOWED_QUALITIES:
        raise HTTPException(
            status_code=400,
            detail=f"Resolusi tidak valid. Pilihan: {', '.join(sorted(ALLOWED_QUALITIES))}.",
        )
    mode = (req.mode or "video").strip().lower()
    if mode not in ALLOWED_MODES:
        raise HTTPException(status_code=400, detail="Mode tidak valid. Pilihan: video, audio.")
    target = (req.target or "guest").strip().lower()
    if target not in ALLOWED_TARGETS:
        raise HTTPException(status_code=400, detail="Target tidak valid. Pilihan: guest, mine.")

    user_creds = None
    if target == "mine":
        tok = get_login(request)
        if not tok:
            raise HTTPException(status_code=401, detail="Silakan login dengan Google dulu.")
        try:
            build_user_credentials(tok)  # refresh bila kedaluwarsa
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=401, detail=f"Sesi Google bermasalah, login ulang. ({e})")
        user_creds = dict(tok)

    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = {
        "id": job_id,
        "url": url,
        "quality": quality,
        "mode": mode,
        "target": target,
        "status": "queued",
        "stage": "menunggu",
        "progress": 0,
    }
    threading.Thread(
        target=run_mirror_job, args=(job_id, url, quality, mode, target, user_creds), daemon=True
    ).start()
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str, authorization: str | None = Header(None)):
    check_auth(authorization)
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")
    return job


app.mount("/", StaticFiles(directory="static", html=True), name="static")
