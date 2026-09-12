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
```

Format hasil: `[n] Judul — Artis (durasi) [videoId]`

### TUI keys

| Konteks | Key | Aksi |
|---|---|---|
| Search | `Enter` | cari / putar yang dipilih |
| Search | `↑` `↓` | navigasi hasil |
| Search | `Esc` | keluar |
| Playing | `Space` | pause / resume |
| Playing | `+` / `-` | volume up / down (mpv saja) |
| Playing | `q` / `Esc` | stop, kembali ke hasil |

## Struktur

```
ytmusic_cli/
  cli.py     # argparse: search | play | tui (default)
  search.py  # YTMusic().search(filter="songs") → Track
  models.py  # dataclass Track + format_track
  player.py  # yt-dlp resolve stream URL → mpv/ffplay, MpvIpc, suspend/resume
  tui.py     # TUI stdlib-only (msvcrt / termios), state murni + render
```

## Roadmap (WIP)

- [ ] Stabilkan playback mpv vs ffplay
- [ ] Queue / playlist
- [ ] Auth (library, liked songs)
- [ ] Tests + CI
- [ ] Rilis PyPI

## Disclaimer

Untuk penggunaan pribadi / edukasi. Hormati ToS YouTube Music. Project ini **belum siap produksi**.
