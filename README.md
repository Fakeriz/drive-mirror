# Drive Mirror

Web app sederhana: tempel link apa pun → file-nya diunduh → otomatis tersimpan ke Google Drive.

Dua opsi di halaman utama:

- **Mode Tamu** — tanpa login. File disimpan di Drive milik app
  (service account), lalu kamu dapat **link publik** yang bisa dibagikan
  ke siapa pun. Dibatasi `GUEST_MAX_MB` (default 500 MB) agar kuota
  Drive app tidak disalahgunakan.
- **Drive Saya** — login dengan Google (OAuth). File masuk ke **Drive
  milikmu sendiri**, di dalam folder "Drive Mirror" (dibuat otomatis).

Mendukung:
- **Link file langsung** — URL yang langsung mengunduh file
  (mp4, mp3, pdf, zip, gambar, dokumen, …)
- **Halaman video** — YouTube, TikTok, Instagram, Facebook, X/Twitter,
  Vimeo, Dailymotion (diekstrak via `yt-dlp`)

Tidak didukung:
- Halaman web biasa / artikel (otomatis ditolak dengan pesan yang jelas —
  itu bukan link file langsung)
- Situs yang butuh login / berbayar

Resolusi video bisa dipilih di form: **360p / 480p / 720p / 1080p / Terbaik**
(default 1080p; "Terbaik" mengambil kualitas tertinggi tapi file bisa besar).

**Mode audio**: pilih "Audio saja (MP3)" di form untuk mengekstrak audio
dari video (mis. lagu dari YouTube) menjadi file mp3 192kbps.
Butuh `ffmpeg` terinstal di server (`sudo apt install ffmpeg` di Ubuntu).

## Cara jalan (lokal / VPS)

```bash
pip install -r requirements.txt
cp .env.example .env   # lalu isi .env
uvicorn app:app --host 0.0.0.0 --port 8000
```

Buka `http://localhost:8000`, tempel link, klik **Mirror ke Drive**.
Progress mengunduh → mengunggah tampil live; kalau selesai, muncul link Drive-nya.

## Setup Google Drive (sekali saja)

### Mode tamu (service account)

1. **Service account**: [Google Cloud Console](https://console.cloud.google.com/)
   → IAM & Admin → Service Accounts → buat account baru
   → Keys → Add Key → JSON → unduh. Tempel seluruh isi JSON ke
   `GOOGLE_SERVICE_ACCOUNT_JSON` dalam **satu baris**.
2. **Folder tujuan**: buat folder di dalam sebuah **Shared Drive**
   (service account tidak punya kuota penyimpanan sendiri, jadi folder
   Drive pribadi tidak bisa dipakai).
3. Di Shared Drive itu, tambahkan email service account
   (`...@....iam.gserviceaccount.com`) sebagai member.
4. Salin ID folder dari URL Drive → isi `DRIVE_FOLDER_ID`.
5. Agar link tamu bisa dibuka publik, pastikan pengaturan sharing
   Shared Drive mengizinkan "anyone with the link".

### Drive saya (login Google / OAuth)

1. Di [Google Cloud Console](https://console.cloud.google.com/)
   → APIs & Services → **Library** → aktifkan **Google Drive API**.
2. → **Credentials** → Create Credentials → **OAuth client ID**
   → tipe **Web application**.
3. Di **Authorized redirect URIs**, tambahkan persis:
   `http://localhost:8000/auth/google/callback`
   (sesuaikan dengan domain/port production, lalu samakan dengan
   `OAUTH_REDIRECT_URI` di `.env`).
4. Isi `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
   `OAUTH_REDIRECT_URI`, dan `SESSION_SECRET` di `.env`.

Izin yang diminta ke user hanya `drive.file` (app hanya bisa
mengakses file yang ia buat sendiri) + data profil dasar.

## Keamanan

- Isi `MIRROR_TOKEN` di `.env` supaya hanya yang tahu token yang bisa
  memakai API. Token dimasukkan sekali di form web (tersimpan di browser).
- Jangan pernah commit file `.env` (sudah ada di `.gitignore`).
- Jalankan di server/VPS pribadi — tool ini mengunduh URL apa pun,
  jadi jangan dibuka ke publik tanpa proteksi tambahan.

## API

| Method | Endpoint | Keterangan |
|---|---|---|
| `GET` | `/api/health` | Cek server hidup |
| `GET` | `/api/me` | Status login Google |
| `GET` | `/auth/google/login` | Mulai login Google (redirect) |
| `GET` | `/auth/google/callback` | Callback OAuth dari Google |
| `POST` | `/auth/logout` | Keluar |
| `POST` | `/api/mirror` | Body: `{"url": "...", "quality": "720p", "mode": "video", "target": "guest"}` → `{"job_id": "..."}`. `quality`: `360p`/`480p`/`720p`/`1080p`/`terbaik` (default `1080p`; diabaikan untuk link file langsung). `mode`: `video` (default) atau `audio` (ekstrak jadi mp3, butuh ffmpeg). `target`: `guest` (default, tanpa login) atau `mine` (perlu login Google) |
| `GET` | `/api/jobs/{id}` | Status: `queued` / `running` / `done` / `error`, `stage`, `progress` (0–100), `drive_link` kalau selesai |

Kalau `MIRROR_TOKEN` diisi, semua endpoint API butuh header
`Authorization: Bearer <token>`.

## Versi Bot Telegram

Selain web, ada juga bot Telegram (`bot.py`): kirim link apa pun ke chat
bot → bot mengunduh → mengunggah ke Drive → membalas link Drive-nya.
Progress tampil live di chat.

Bot berjalan dalam **mode tamu** (tanpa login Google), jadi
`GUEST_MAX_MB` berlaku. Untuk "Drive saya", pakai versi web.

```bash
# 1. Chat ke @BotFather di Telegram -> /newbot -> salin tokennya
# 2. Isi TELEGRAM_BOT_TOKEN di .env (opsional: TELEGRAM_ALLOWED_IDS)
python bot.py
```

Bot memakai *polling*, jadi tidak butuh HTTPS/domain — cukup server/VPS
yang jalan terus. Web (`uvicorn app:app`) dan bot bisa jalan bersamaan.

Perintah bot:
- `/start` / `/bantuan` — panduan
- `/resolusi <360p|480p|720p|1080p|terbaik>` — atur resolusi video
- `/tipe <video|audio>` — video biasa atau ekstrak jadi MP3

## Struktur

```
app.py            # FastAPI: endpoint + OAuth Google + serve halaman web
bot.py            # Bot Telegram (polling): kirim link -> balas link Drive
mirror.py         # logika unduh (langsung / yt-dlp) + orkestrasi job
drive_client.py   # upload resumable (service account & OAuth user)
oauth.py          # helper login Google (OAuth 2.0)
static/index.html # UI web (Bahasa Indonesia): pilih mode -> form mirror
```
