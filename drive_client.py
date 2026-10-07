"""Upload ke Google Drive — dua jalur kredensial:

- Mode tamu : service account milik app (butuh GOOGLE_SERVICE_ACCOUNT_JSON)
- Drive saya: OAuth milik user yang login (token dari oauth.py)
"""
import json
import os

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from oauth import build_user_credentials

SA_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def sa_service():
    """Service Drive via service account (mode tamu)."""
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON belum diisi. Lihat .env.example / README."
        )
    info = json.loads(raw)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SA_SCOPES)
    return build("drive", "v3", credentials=creds)


def user_service(creds_dict: dict):
    """Service Drive via OAuth user yang login (mode Drive saya)."""
    creds = build_user_credentials(creds_dict)
    return build("drive", "v3", credentials=creds)


def upload_file(service, path: str, filename: str, folder_id: str, progress_cb=None) -> dict:
    """Upload resumable. Return dict {id, name, webViewLink}."""
    media = MediaFileUpload(path, resumable=True)
    body = {"name": filename}
    if folder_id:
        body["parents"] = [folder_id]
    req = service.files().create(
        body=body,
        media_body=media,
        fields="id,name,webViewLink",
        supportsAllDrives=True,
    )
    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status and progress_cb:
            progress_cb(status.progress() or 0)
    return resp


def share_anyone(service, file_id: str):
    """Bikin file bisa dibuka siapa pun yang punya link (untuk mode tamu)."""
    service.permissions().create(
        fileId=file_id,
        body={"type": "anyone", "role": "reader"},
        supportsAllDrives=True,
    ).execute()


def ensure_folder(service, name: str) -> str:
    """Cari folder 'Drive Mirror' di Drive user, buat bila belum ada. Return folder id."""
    q = (
        f"name='{name}' and mimeType='application/vnd.google-apps.folder' "
        "and trashed=false"
    )
    res = (
        service.files()
        .list(
            q=q,
            fields="files(id)",
            pageSize=1,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    created = (
        service.files()
        .create(
            body={"name": name, "mimeType": "application/vnd.google-apps.folder"},
            fields="id",
            supportsAllDrives=True,
        )
        .execute()
    )
    return created["id"]
