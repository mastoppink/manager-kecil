# manager-kecil

Panel web untuk mengelola banyak channel YouTube dari satu server: render video visualizer musik secara massal, membuat Shorts, memotong clip, menjalankan siaran langsung 24/7, lalu menjadwalkan upload otomatis.

Tampilannya mengikuti gaya YouTube Studio dan bisa dibuka dari browser di laptop maupun HP.

## Fitur

| Modul | Fungsi |
|---|---|
| **Factory** | Render video visualizer panjang (audio + background + efek) secara batch, lalu upload terjadwal ke YouTube. |
| **Upload langsung** | Upload video yang sudah jadi (dari folder *Video siap upload* di Galeri Aset) ke YouTube tanpa render: judul dari nama file atau Bank Judul, jadwal berselang, playlist, dan thumbnail bernama sama otomatis. File dihapus dari server setelah tayang. |
| **Shorts** | Produksi video vertikal 9:16 dan jadwal upload ke YouTube Shorts dan Facebook Reels, dengan pratinjau efek secara langsung. |
| **Clipper** | Ambil momen terbaik dari video YouTube, crop 9:16, beri subtitle otomatis (Groq Whisper atau Gemini), lalu jadwalkan upload. |
| **Live** | Siaran langsung YouTube multi-channel. Base video dirender di antrean prioritas, dan modul render lain dijeda selama siaran. |
| **Antrean** | Pantau semua antrean render dan upload dari setiap modul, dikelompokkan per channel. |
| **Analytics** | Views, subscriber, estimasi jam tayang dan pendapatan, progres syarat monetisasi, serta video teratas. |
| **Settings** | Kelola channel, halaman Facebook, galeri aset per channel, bank judul, dan notifikasi Telegram. |

Fitur lain:
- **Mode konten per channel** (*Settings → Channel YouTube*):
  - **Content ID:** footage background boleh dipakai berulang (diacak bergiliran supaya tidak kembar berurutan), audio diambil dari folder Audio CID.
  - **AdSense:** setiap background, background Shorts, dan video sumber hanya dipakai sekali untuk menghindari konten berulang. File yang sudah masuk video yang berhasil dipindah ke subfolder `_terpakai/`; kalau render atau upload gagal, file kembali ke stok. Batch ditolak sejak awal bila stok tidak cukup. Audio diambil dari folder Audio non-CID.
  - Sisa stok dan file terpakai bisa dilihat, dikembalikan ke stok, atau dihapus di *Settings → Galeri Aset*.
- **Impor dari Google Drive** (*Settings → Galeri Aset → Dari Drive* di setiap folder): tempel link file atau folder Drive dengan akses "Siapa saja yang memiliki link", pilih file, lalu server mengunduhnya di background dengan progres per file. File yang sudah ada dilewati, file yang izin download-nya dimatikan pemilik ditandai gagal, dan impor berhenti otomatis kalau ruang disk tinggal kurang dari 2 GB.
- **Kuota API per channel.** Setiap channel memakai `client_secret.json` miliknya sendiri, sehingga kuota YouTube API tidak saling berebut. Satu file secret juga boleh dipakai beberapa channel.
- **Upload bisa dilanjutkan.** Upload yang terputus dilanjutkan dari posisi terakhir.
- **Cooldown otomatis.** Kalau kuota atau limit upload habis, channel diberi jeda otomatis, dan jeda itu tetap berlaku setelah server restart.
- **Notifikasi Telegram** saat render selesai atau gagal, dan saat stok jadwal video hampir habis.

## Kebutuhan

- VPS Ubuntu/Debian dengan akses root. CPU dan RAM yang lebih besar mempercepat render.
- Python 3.10 atau lebih baru.
- FFmpeg dan FFprobe. Keduanya dipasang otomatis oleh `install.sh`.
- Akun Google Cloud untuk YouTube Data API. Lihat [Menyiapkan YouTube API](#menyiapkan-youtube-api).

## Instalasi di VPS

Jalankan sebagai root:

```bash
curl -fsSL https://raw.githubusercontent.com/mastoppink/manager-kecil/main/install.sh -o install.sh
bash install.sh
```

Script ini memasang dependensi sistem, mengambil kode ke `/root/manager-kecil`, membuat virtualenv, lalu menjalankan aplikasi sebagai service systemd `manager-kecil` yang aktif 24/7.

Setelah selesai, buka `http://IP-VPS:5000`. Saat pertama kali dibuka, kamu akan diminta membuat PIN untuk masuk.

### Memperbarui

**Dari panel (disarankan):** buka **Settings → Sistem → Pembaruan aplikasi**. Panel menampilkan daftar perubahan baru di GitHub. Klik **Perbarui sekarang** untuk mengambil kode terbaru, memasang library baru kalau `requirements.txt` berubah, lalu me-restart service otomatis. Task Factory yang sedang berjalan akan dilanjutkan setelah aplikasi menyala lagi. Pembaruan otomatis dinonaktifkan kalau ada file kode yang diubah langsung di server.

**Lewat terminal:** jalankan lagi `bash install.sh`.

Kedua cara di atas mengambil kode terbaru dari GitHub, dan data berikut **tidak dihapus**:

- `channels_db.json` (channel dan token)
- `secrets/` (client secret)
- `config.json` (PIN)
- `uploads/` (aset galeri)
- file data lain yang tercantum di `.gitignore`

### Perintah service

```bash
systemctl status manager-kecil     # status
systemctl restart manager-kecil    # restart
journalctl -u manager-kecil -f     # log langsung
```

## Menyiapkan YouTube API

Ulangi langkah ini untuk setiap project Google Cloud yang ingin dipakai. Idealnya satu project untuk setiap channel.

1. Buka [Google Cloud Console](https://console.cloud.google.com/), lalu buat project baru.
2. Aktifkan **YouTube Data API v3** di menu *APIs & Services → Library*.
3. Atur *OAuth consent screen*:
   - Tipe *External*.
   - Tambahkan email pemilik channel sebagai *Test user*.
   - Lalu ubah status publikasinya ke **In production**. Kalau masih *Testing*, token akan kedaluwarsa setiap 7 hari.
4. Buat *Credentials → OAuth client ID* dengan tipe aplikasi **TVs and Limited Input devices**, lalu unduh file JSON-nya.
5. Di manager-kecil, buka **Settings → Channel YouTube → Tautkan channel**, lalu pilih file JSON tadi.
6. Buka link aktivasi yang muncul di browser yang sudah login ke akun channel, masukkan kodenya, lalu izinkan akses.

Untuk mengganti secret channel yang sudah terhubung, klik ikon kunci di baris channel tersebut, lalu tautkan ulang.

## Integrasi opsional

- **Facebook Reels:** di *Settings → Facebook*, masukkan Page ID dan Page Access Token permanen.
- **Telegram:** di *Settings → Sistem*, masukkan bot token dan chat ID.
- **Subtitle otomatis (Clipper):** masukkan API key Groq atau Gemini di halaman Clipper. Key ini disimpan di browser.
- **Cookie yt-dlp (Clipper):** tempel cookie format Netscape kalau unduhan YouTube diblokir.

## Menjalankan secara lokal

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/python app.py
```

Aplikasi berjalan di `http://localhost:5000`. Pastikan `ffmpeg` dan `ffprobe` sudah terpasang, misalnya lewat `brew install ffmpeg` di macOS.

> Menjalankan `app.py` langsung juga ikut menyalakan worker render, upload, dan live. Untuk pengujian, matikan worker dengan `MANAGER_KECIL_START_WORKERS=0`.

### Test

```bash
MANAGER_KECIL_START_WORKERS=0 venv/bin/python -m unittest discover -s tests
```

## Struktur folder

```
app.py              Aplikasi Flask: halaman, API, worker Factory, OAuth YouTube
core/               Modul bersama: penyimpanan JSON, status job, upload lanjutan, auth channel
clipper/            Modul Clipper (yt-dlp, AI subtitle, ffmpeg)
shorts/             Modul Shorts (render 9:16 dan worker)
live/               Modul Live streaming
templates/          Halaman HTML (Jinja); _studio_shell.html = header & sidebar bersama
static/css/         studio.css: sistem desain bersama
static/js/          studio.js (toast, dialog) dan clipper.js
tests/              Unit test
install.sh          Instalasi dan update di VPS
```

## Data dan cadangan

Semua data disimpan sebagai file di folder aplikasi dan tidak ikut ke Git:

| File/folder | Isi |
|---|---|
| `channels_db.json` | Daftar channel dan token OAuth |
| `secrets/` | File `client_secret` per project Google Cloud |
| `config.json` | PIN login dan secret key sesi |
| `tasks_db.json` | Antrean dan riwayat Factory |
| `uploads/` | Aset galeri per channel (audio, background, thumbnail) |

Untuk mencadangkan channel, gunakan **Settings → Channel YouTube → Export**. Simpan file hasil export di tempat yang aman, karena isinya mencakup token akses channel.

## Catatan keamanan

- Panel dilindungi PIN, tetapi berjalan lewat HTTP biasa di port 5000. Untuk pemakaian jangka panjang, pasang reverse proxy dengan HTTPS (misalnya Caddy atau Nginx + Let's Encrypt), atau batasi akses port 5000 lewat firewall.
- Jangan pernah meng-commit `channels_db.json`, `config.json`, `secrets/`, atau file cookie. Semuanya sudah tercantum di `.gitignore`.
