# ytmusic-cli 🚧 WIP

> **⚠️ WORK IN PROGRESS — project masih dalam tahap pengembangan aktif. API, CLI flags, dan struktur kode bisa berubah sewaktu-waktu tanpa pemberitahuan.**

YouTube Music CLI sederhana: search + stream playback + TUI interaktif (stdlib-only, Windows + POSIX).

## Status

🚧 **WIP** — yang sudah jalan: `search`, `play` (id/URL/query), `tui` (queue+autoplay+progress), `history`, `doctor`. Yang belum stabil: packaging, auth library.

## Requirements

- Python >= 3.10
- Player eksternal (salah satu): `mpv` (rekomendasi, dukung pause/volume via IPC) atau `ffplay` (fallback, tanpa remote control)
- Koneksi internet (ytmusicapi + yt-dlp resolve stream)

## Install

```bash
pip install -e .
# atau: pip install ytmusicapi "yt-dlp>=2024.8.0"
```

## Usage

```bash
ytmusic                     # tanpa argumen = buka TUI
ytmusic tui                 # mode interaktif: ketik query, ↑↓ pilih, Enter putar antrean
ytmusic search "ado" -n 5   # cari lagu, tampilkan 5 hasil
ytmusic play <videoId|URL>  # putar audio (Ctrl-C berhenti)
ytmusic play "ado usu"      # kata kunci: pilih nomor hasil, --first langsung putar #1
ytmusic history [-n 5]      # lihat riwayat putar (cache 30 hari / 30 lagu)
ytmusic history --remove 2  # hapus entri nomor 2 (1 = terbaru)
ytmusic history --clear     # hapus seluruh riwayat
ytmusic history --json      # riwayat sebagai JSON
ytmusic doctor              # cek mpv/ffplay, yt-dlp, ytmusicapi, lokasi riwayat
```

Format hasil: `[n] Judul — Artis (durasi) [videoId]`

### TUI keys

| Konteks | Key | Aksi |
|---|---|---|
| Search | `Enter` | cari / putar antrean dari posisi terpilih (autoplay lanjut) |
| Search | `↑` `↓` | navigasi hasil |
| Search | `Ctrl+R` | panel riwayat: `↑` `↓` pilih, `Enter` putar, `x` hapus entri, `C` bersihkan, `Esc` tutup |
| Search | `Ctrl+Q` | panel antrean: `↑` `↓` pilih, `Enter` putar, `x` hapus, `u`/`d` susun, `S` simpan, `L` muat |
| Search | `Ctrl+A` | tambah lagu terpilih ke antrean |
| Playing | `Space` | pause / resume |
| Playing | `n` / `p` | next / prev dalam antrean |
| Playing | `x` | hapus lagu kini dari antrean, lanjut berikut |
| Playing | `s` | acak nyala/mati (shuffle lagu berikut) |
| Playing | `r` | ulangi: mati → semua → satu |
| Playing | `+` / `-` | volume up / down (mpv saja) |
| Playing | `←` / `→` | seek -5 / +5 detik (mpv saja) |
| Playing | `q` / `Esc` | stop, kembali ke hasil (tombol lain diabaikan) |

Volume diingat antar trek dan antar sesi (settings.json); mpv diluncurkan dengan
`--volume` sehingga trek baru langsung mulai pada volume yang dipilih (tidak ada
percikan 100% di awal trek).

Progress `pos / dur` + nomor antrean `[i/N]` tampil saat mpv dipakai; search/play retry otomatis bila network hiccup; lagu berikutnya di-prefetch selagi memutar.

## Struktur

```
ytmusic_cli/
  cli.py     # argparse: search | play | tui (default) | history | doctor
  search.py  # YTMusic().search(filter="songs") → Track, retry backoff
  models.py  # dataclass Track + format_track
  player.py  # yt-dlp resolve stream URL → mpv/ffplay, MpvIpc, suspend/resume, prefetch
  history.py # riwayat putar: cache JSON 30 hari / 30 lagu, prune + dedupe
  tui.py     # TUI stdlib-only (msvcrt / termios), queue + progress + render murni
  doctor.py  # cek lingkungan: python, mpv/ffplay, yt-dlp, ytmusicapi, riwayat
```

## Riwayat

Setiap lagu yang diputar (via `play` maupun TUI) tercatat otomatis.
Di TUI tekan `Ctrl+R` untuk panel riwayat yang siap pilih + `Enter` putar.
Cache: `%LOCALAPPDATA%/ytmusic-cli/history.json` (Windows) atau
`$XDG_CACHE_HOME/ytmusic-cli/history.json` (`~/.cache` fallback di POSIX);
env `YTMUSIC_CLI_HISTORY_FILE` menimpa lokasi.
Volume yang dipilih di TUI disimpan di `settings.json` di direktori yang sama
(env `YTMUSIC_CLI_SETTINGS_FILE` menimpa); nilai dipakai lagi di trek berikutnya
dan di sesi berikutnya.

## Roadmap (WIP)

- [ ] Stabilkan playback mpv vs ffplay
- [ ] Queue / playlist
- [ ] Auth (library, liked songs)
- [ ] Tests + CI
- [ ] Rilis PyPI

## Disclaimer

Untuk penggunaan pribadi / edukasi. Hormati ToS YouTube Music. Project ini **belum siap produksi**.
