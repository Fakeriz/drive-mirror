"""Drive Mirror — versi Bot Telegram.

Cara pakai: kirim link apa pun ke chat bot -> bot mengunduh ->
mengunggah ke Google Drive -> membalas link Drive-nya.

Bot berjalan dalam MODE TAMU (tanpa login Google): file masuk ke Drive
milik app (service account) dengan link publik, dibatasi GUEST_MAX_MB.
Untuk "Drive saya" (OAuth), pakai versi web.

Jalankan:  python bot.py   (butuh TELEGRAM_BOT_TOKEN di .env)
"""
import asyncio
import os
import re
import threading
import uuid

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

from mirror import jobs, run_mirror_job

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
ALLOWED_IDS = {
    int(x) for x in os.environ.get("TELEGRAM_ALLOWED_IDS", "").split(",") if x.strip().isdigit()
}

URL_RE = re.compile(r"https?://[^\s<>\"]+")
QUALITIES = ("360p", "480p", "720p", "1080p", "terbaik")
MODES = ("video", "audio")

# user_id -> {"quality": ..., "mode": ...}
prefs: dict = {}


def get_prefs(user_id: int) -> dict:
    return prefs.setdefault(user_id, {"quality": "1080p", "mode": "video"})


def allowed(user_id: int) -> bool:
    return not ALLOWED_IDS or user_id in ALLOWED_IDS


def bar(pct: float) -> str:
    filled = int(pct / 10)
    return "█" * filled + "░" * (10 - filled)


def render_status(job: dict) -> str:
    p = job.get("progress", 0)
    return f"⏳ {job.get('stage', 'memproses')}…\n{bar(p)} {p:.0f}%"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update.effective_user.id):
        return
    p = get_prefs(update.effective_user.id)
    await update.message.reply_text(
        "👋 Halo! Kirim link apa pun ke saya, nanti saya mirror ke Google Drive "
        "dan balas link-nya.\n\n"
        "Mendukung: link file langsung (mp4, mp3, pdf, …) dan video "
        "(YouTube, TikTok, Instagram, X).\n\n"
        f"Setelan kamu: resolusi {p['quality']}, tipe {p['mode']}.\n\n"
        "Perintah:\n"
        "/resolusi <360p|480p|720p|1080p|terbaik>\n"
        "/tipe <video|audio> — audio = ekstrak jadi MP3\n"
        "/bantuan"
    )


async def bantuan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def set_resolusi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update.effective_user.id):
        return
    if not context.args or context.args[0].lower() not in QUALITIES:
        await update.message.reply_text(
            f"Pilih: {', '.join(QUALITIES)}\nContoh: /resolusi 720p"
        )
        return
    get_prefs(update.effective_user.id)["quality"] = context.args[0].lower()
    await update.message.reply_text(f"✅ Resolusi diset ke {context.args[0].lower()}.")


async def set_tipe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update.effective_user.id):
        return
    if not context.args or context.args[0].lower() not in MODES:
        await update.message.reply_text("Pilih: video atau audio\nContoh: /tipe audio")
        return
    get_prefs(update.effective_user.id)["mode"] = context.args[0].lower()
    await update.message.reply_text(
        f"✅ Tipe diset ke {context.args[0].lower()}."
        + (" (audio diekstrak jadi MP3)" if context.args[0].lower() == "audio" else "")
    )


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not allowed(user_id):
        return
    text = update.message.text or ""
    m = URL_RE.search(text)
    if not m:
        await update.message.reply_text(
            "Kirim link yang valid ya. Contoh:\nhttps://youtu.be/xxxx"
        )
        return
    url = m.group(0).rstrip(").,!")
    if not url.startswith(("http://", "https://")):
        return

    p = get_prefs(user_id)
    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = {
        "id": job_id,
        "url": url,
        "quality": p["quality"],
        "mode": p["mode"],
        "target": "guest",
        "status": "queued",
        "stage": "menunggu",
        "progress": 0,
    }
    threading.Thread(
        target=run_mirror_job,
        args=(job_id, url, p["quality"], p["mode"], "guest", None),
        daemon=True,
    ).start()

    status_msg = await update.message.reply_text("⏳ Menyiapkan…")
    last = ""
    for _ in range(1800):  # maks ~60 menit
        job = jobs.get(job_id)
        if not job:
            break
        txt = render_status(job)
        if txt != last:
            try:
                await status_msg.edit_text(txt)
            except Exception:
                pass
            last = txt
        if job["status"] in ("done", "error"):
            break
        await asyncio.sleep(2)

    job = jobs.get(job_id, {})
    if job.get("status") == "done":
        name = (job.get("filename") or "Buka di Google Drive").replace("<", "&lt;")
        link = job.get("drive_link", "")
        try:
            await status_msg.edit_text(
                f"✅ Selesai!\n<a href=\"{link}\">{name}</a>",
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception:
            await update.message.reply_text(f"✅ Selesai!\n{link}")
    else:
        err = job.get("error", "waktu habis atau job hilang.")
        try:
            await status_msg.edit_text(f"❌ Gagal: {err}")
        except Exception:
            pass


def main():
    if not TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN belum diisi di .env")
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("bantuan", bantuan))
    app.add_handler(CommandHandler("help", bantuan))
    app.add_handler(CommandHandler("resolusi", set_resolusi))
    app.add_handler(CommandHandler("tipe", set_tipe))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    print("Bot jalan (polling). Kirim /start di Telegram untuk mencoba.")
    app.run_polling()


if __name__ == "__main__":
    main()
