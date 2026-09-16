# ytmusic-cli 🚧 WIP

> **⚠️ WORK IN PROGRESS — project masih dalam tahap pengembangan aktif. API, CLI flags, dan struktur kode bisa berubah sewaktu-waktu tanpa pemberitahuan.**

YouTube Music CLI sederhana: search + stream playback + TUI interaktif (stdlib-only, Windows + POSIX).

## Status

🚧 **WIP** — yang sudah jalan: `search`, `play`, `tui`. Yang belum stabil: kontrol volume/playback lintas player, error handling, packaging.

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
ytmusic tui                 # mode interaktif: ketik query, ↑↓ pilih, Enter putar
ytmusic search "ado" -n 5   # cari lagu, tampilkan 5 hasil
ytmusic play <videoId|URL>  # putar audio (Ctrl-C berhenti)
ytmusic history [-n 5]      # lihat riwayat putar (cache 30 hari / 30 lagu)
ytmusic history --clear     # hapus riwayat
ytmusic history --json      # riwayat sebagai JSON

Format hasil: `[n] Judul — Artis (durasi) [videoId]`

### TUI keys

| Konteks | Key | Aksi |
|---|---|---|
| Search | `Enter` | cari / putar yang dipilih |
| Search | `↑` `↓` | navigasi hasil |
| Search | `Ctrl+R` | panel riwayat: `↑` `↓` pilih, `Enter` putar, `Esc` tutup |
| Playing | `Space` | pause / resume |
| Playing | `+` / `-` | volume up / down (mpv saja) |
| Playing | `←` / `→` | seek -5 / +5 detik (mpv saja) |
| Playing | `q` / `Esc` | stop, kembali ke hasil (tombol lain diabaikan) |

## Struktur

```
ytmusic_cli/
  cli.py     # argparse: search | play | tui (default)
  search.py  # YTMusic().search(filter="songs") → Track
  models.py  # dataclass Track + format_track
  player.py  # yt-dlp resolve stream URL → mpv/ffplay, MpvIpc, suspend/resume
  history.py # riwayat putar: cache JSON 30 hari / 30 lagu, prune + dedupe
  tui.py     # TUI stdlib-only (msvcrt / termios), state murni + render
```

## Riwayat

Setiap lagu yang diputar (via `play` maupun TUI) tercatat otomatis.
Di TUI tekan `Ctrl+R` untuk panel riwayat yang siap pilih + `Enter` putar.
Cache: `%LOCALAPPDATA%/ytmusic-cli/history.json` (Windows) atau
`$XDG_CACHE_HOME/ytmusic-cli/history.json` (`~/.cache` fallback di POSIX);
env `YTMUSIC_CLI_HISTORY_FILE` menimpa lokasi.

## Roadmap (WIP)

- [ ] Stabilkan playback mpv vs ffplay
- [ ] Queue / playlist
- [ ] Auth (library, liked songs)
- [ ] Tests + CI
- [ ] Rilis PyPI

## Disclaimer

Untuk penggunaan pribadi / edukasi. Hormati ToS YouTube Music. Project ini **belum siap produksi**.
