"""Inti pekerjaan mirror: unduh dari URL -> upload ke Google Drive."""
import os
import re
import shutil
import tempfile
import threading
from urllib.parse import unquote, urlparse

import requests

from drive_client import ensure_folder, sa_service, share_anyone, upload_file, user_service

jobs: dict = {}
_jobs_lock = threading.Lock()

GUEST_FOLDER_FALLBACK = "Drive Mirror"

# Situs yang butuh ekstraksi dulu (bukan link file langsung)
VIDEO_DOMAINS = (
    "youtube.com",
    "youtu.be",
    "tiktok.com",
    "instagram.com",
    "facebook.com",
    "fb.watch",
    "twitter.com",
    "x.com",
    "vimeo.com",
    "dailymotion.com",
)

CHUNK = 1024 * 256


def set_job(job_id: str, **fields):
    with _jobs_lock:
        if job_id in jobs:
            jobs[job_id].update(fields)


def sanitize_filename(name: str) -> str:
    name = unquote(name or "")
    name = re.sub(r'[\\/:*?"<>|]', "_", name).strip().strip(".")
    return name[:180] or "file"


def looks_like_video_page(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(d in host for d in VIDEO_DOMAINS)


def _progress_download(job_id: str, done: int, total: int):
    if total:
        set_job(job_id, stage="mengunduh", progress=round(done / total * 50, 1))


def download_direct(url: str, dest: str, job_id: str) -> str:
    """Unduh link file langsung dengan streaming."""
    with requests.get(
        url, stream=True, timeout=60, headers={"User-Agent": "Mozilla/5.0"}
    ) as r:
        r.raise_for_status()

        ct = r.headers.get("content-type", "").split(";")[0].strip().lower()
        if ct.startswith("text/html"):
            raise RuntimeError(
                "URL ini adalah halaman web, bukan link file langsung. "
                "Untuk video, pakai link dari situs yang didukung "
                "(YouTube, TikTok, Instagram, X, Facebook, Vimeo, Dailymotion)."
            )

        total = int(r.headers.get("content-length", 0) or 0)

        cd = r.headers.get("content-disposition", "")
        m = re.search(r"filename\*=UTF-8''([^;]+)|filename=\"([^\"]+)\"", cd)
        if m:
            fname = m.group(1) or m.group(2)
        else:
            fname = os.path.basename(urlparse(url).path) or "file"
        fname = sanitize_filename(fname)

        if "." not in fname:
            ct = r.headers.get("content-type", "").split(";")[0].strip().lower()
            ext = {
                "video/mp4": ".mp4",
                "audio/mpeg": ".mp3",
                "application/pdf": ".pdf",
                "application/zip": ".zip",
                "image/jpeg": ".jpg",
                "image/png": ".png",
            }.get(ct, "")
            fname += ext

        path = os.path.join(dest, fname)
        done = 0
        with open(path, "wb") as f:
            for chunk in r.iter_content(CHUNK):
                if chunk:
                    f.write(chunk)
                    done += len(chunk)
                    _progress_download(job_id, done, total)
        if total == 0:
            set_job(job_id, stage="mengunduh", progress=50)
        return path


def _format_for(quality: str) -> str:
    """Petakan pilihan resolusi ke format yt-dlp."""
    if quality == "terbaik":
        return "bv*+ba/b"
    h = quality.replace("p", "")
    return f"bv*[height<={h}]+ba/b[height<={h}]/b"


def download_video(
    url: str, dest: str, job_id: str, quality: str = "1080p", mode: str = "video"
) -> str:
    """Ekstrak & unduh video (YouTube, TikTok, dll) via yt-dlp.

    mode="audio": hanya ambil audio lalu konversi ke mp3 (butuh ffmpeg).
    """
    import yt_dlp

    before = set(os.listdir(dest))

    def hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            _progress_download(job_id, d.get("downloaded_bytes", 0), total)

    opts = {
        "outtmpl": os.path.join(dest, "%(title).100s.%(ext)s"),
        "progress_hooks": [hook],
        "quiet": True,
        "no_warnings": True,
        # client android lolos dari bot-check YouTube tanpa login
        "extractor_args": {"youtube": {"player_client": ["android"]}},
        # darurat saja (mis. di balik proxy MITM): YTDLP_NOCHECKCERT=1
        "nocheckcertificate": os.environ.get("YTDLP_NOCHECKCERT") == "1",
    }
    if mode == "audio":
        opts["format"] = "ba/b"
        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]
    else:
        opts["format"] = _format_for(quality)
        opts["merge_output_format"] = "mp4"
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(url, download=True)

    new_files = [f for f in os.listdir(dest) if f not in before]
    if not new_files:
        raise RuntimeError("yt-dlp tidak menghasilkan file.")
    new_files.sort(key=lambda f: os.path.getmtime(os.path.join(dest, f)), reverse=True)
    return os.path.join(dest, new_files[0])


def run_mirror_job(
    job_id: str,
    url: str,
    quality: str = "1080p",
    mode: str = "video",
    target: str = "guest",
    user_creds: dict | None = None,
):
    tmp = tempfile.mkdtemp(prefix="mirror_")
    try:
        set_job(job_id, status="running", stage="mengunduh", progress=0)
        if looks_like_video_page(url):
            path = download_video(url, tmp, job_id, quality, mode)
        else:
            path = download_direct(url, tmp, job_id)

        if target == "guest":
            max_mb = int(os.environ.get("GUEST_MAX_MB", "500") or 500)
            size_mb = os.path.getsize(path) / (1024 * 1024)
            if size_mb > max_mb:
                raise RuntimeError(
                    f"File {size_mb:.0f} MB melebihi batas mode tamu ({max_mb} MB). "
                    "Coba resolusi lebih kecil, mode audio, atau login Google "
                    "untuk memakai Drive sendiri."
                )

        set_job(job_id, stage="mengunggah ke Drive", progress=50)

        if target == "mine":
            if not user_creds:
                raise RuntimeError("Kredensial Google tidak ditemukan. Login ulang.")
            service = user_service(user_creds)
            folder_id = ensure_folder(service, GUEST_FOLDER_FALLBACK)
            meta = upload_file(
                service,
                path,
                os.path.basename(path),
                folder_id,
                progress_cb=lambda p: set_job(job_id, progress=round(50 + p * 50, 1)),
            )
        else:
            service = sa_service()
            folder_id = os.environ.get("DRIVE_FOLDER_ID", "").strip()
            meta = upload_file(
                service,
                path,
                os.path.basename(path),
                folder_id,
                progress_cb=lambda p: set_job(job_id, progress=round(50 + p * 50, 1)),
            )
            # mode tamu: link harus bisa dibuka tanpa login
            share_anyone(service, meta["id"])

        set_job(
            job_id,
            status="done",
            stage="selesai",
            progress=100,
            filename=meta.get("name"),
            drive_link=meta.get("webViewLink"),
            drive_id=meta.get("id"),
        )
    except Exception as e:  # noqa: BLE001 - tampilkan apa adanya ke user
        set_job(job_id, status="error", stage="gagal", error=str(e)[:500])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
