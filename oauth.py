"""Login dengan Google (OAuth 2.0) — untuk mode 'Drive saya'.

Alur: /auth/google/login -> Google -> /auth/google/callback -> token
disimpan di server (memory), session cookie hanya menyimpan sid acak.
"""
import os
import time
import urllib.parse

import requests
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials as UserCredentials

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

OAUTH_SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/drive.file",
]


def oauth_configured() -> bool:
    return bool(
        os.environ.get("GOOGLE_CLIENT_ID")
        and os.environ.get("GOOGLE_CLIENT_SECRET")
        and os.environ.get("OAUTH_REDIRECT_URI")
    )


def build_auth_url(state: str) -> str:
    params = {
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "redirect_uri": os.environ["OAUTH_REDIRECT_URI"],
        "response_type": "code",
        "scope": " ".join(OAUTH_SCOPES),
        "access_type": "offline",  # supaya dapat refresh_token
        "prompt": "consent",
        "state": state,
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(params)


def exchange_code(code: str) -> dict:
    """Tukar authorization code jadi token. Return dict creds."""
    r = requests.post(
        TOKEN_URL,
        data={
            "code": code,
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "redirect_uri": os.environ["OAUTH_REDIRECT_URI"],
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if "refresh_token" not in data:
        # access_type=offline + prompt=consent seharusnya memberi refresh_token
        pass
    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token"),
        "expiry": time.time() + data.get("expires_in", 3600),
    }


def get_userinfo(access_token: str) -> dict:
    r = requests.get(
        USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def build_user_credentials(creds_dict: dict) -> UserCredentials:
    """Bangun Credentials dari dict simpanan; refresh otomatis bila kedaluwarsa."""
    creds = UserCredentials(
        token=creds_dict.get("access_token"),
        refresh_token=creds_dict.get("refresh_token"),
        token_uri=TOKEN_URL,
        client_id=os.environ.get("GOOGLE_CLIENT_ID"),
        client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
        scopes=OAUTH_SCOPES,
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleRequest())
        creds_dict["access_token"] = creds.token
        creds_dict["expiry"] = (
            creds.expiry.timestamp() if creds.expiry else time.time() + 3600
        )
    elif creds_dict.get("expiry") and time.time() > creds_dict["expiry"] - 60:
        # fallback bila objek creds tidak tahu expiry-nya
        if creds.refresh_token:
            creds.refresh(GoogleRequest())
            creds_dict["access_token"] = creds.token
            creds_dict["expiry"] = time.time() + 3600
    return creds
