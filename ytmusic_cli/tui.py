"""TUI stdlib-only untuk ytmusic-cli (Windows + POSIX).

Kontrol: ketik query + Enter = cari | ↑↓ = pilih | Enter = putar antrean dari posisi terpilih |
Ctrl+R = riwayat (↑↓ pilih, Enter putar, x hapus entri, C hapus semua) | spasi = jeda/lanjut | n/p = next/prev |
s = acak | r = ulangi (mati → semua → satu) |
-/+ = volume (butuh mpv) | ←/→ = seek -5/+5 dtk (butuh mpv) |
q / Esc = berhenti | Esc di daftar = keluar.
Ctrl+Q = panel antrean (↑↓ pilih, Enter putar, x hapus, u/d susun, S simpan, L muat) |
Ctrl+A = tambah lagu terpilih ke antrean | x saat memutar = hapus lagu kini.
Volume diingat antar trek dan antar sesi (settings.json).
"""

import os
import random
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .models import Track
from .history import (
    HistoryEntry,
    add_history,
    clear_history,
    format_played_at,
    load_history,
    load_queue,
    load_settings,
    remember_volume,
    remove_history_at,
    save_queue,
)
from .player import MpvIpc, player_cmd, resolve_stream_url, resume_process, start_player, stop_player, suspend_process
from .search import search_tracks

try:
    import msvcrt

    _WINDOWS = True
except ImportError:
    _WINDOWS = False

LEFT, RIGHT, UP, DOWN, ENTER, ESC, BACKSPACE = "left", "right", "up", "down", "enter", "esc", "backspace"
CTRL_R = "\x12"  # Ctrl+R = tampilkan/sembunyikan panel riwayat
CTRL_Q = "\x11"  # Ctrl+Q = panel antrean
CTRL_A = "\x01"  # Ctrl+A = tambah lagu terpilih ke antrean

RepeatMode = Literal["off", "all", "one"]  # ulangi: mati | semua lagu | lagu ini

# ── Tampilan ────────────────────────────────────────────────────────────────
# Warna ANSI 256. paint() menjadi polos (tanpa escape) bila NO_COLOR / bukan tty,
# jadi output `ytmusic tui | cat` maupun CI tetap bersih.
C_TITLE = 51  # cyan terang — judul lagu
C_ARTIST = 245  # abu hangat — artis / metadata
C_DIM = 240  # abu gelap — pemisah, nomor, durasi
C_ACCENT = 213  # magenta — penanda ▶ / aksen
C_OK = 35  # hijau — status positif, tag aktif
C_WARN = 214  # jingga — jeda, peringatan
C_ERR = 196  # merah — error
C_SEL_BG = 62  # biru-kelabu — latar baris terpilih
C_SEL_FG = 15  # putih — teks baris terpilih

_SPINNER = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
_WIDE = {"W", "F"}  # East Asian Wide / Fullwidth → lebar 2 kolom


def _color() -> bool:
    """True bila output layar mendukung warna (hormati NO_COLOR / FORCE_COLOR)."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    try:
        return bool(sys.stdout.isatty())
    except Exception:
        return False


def paint(text: str, *codes: int) -> str:
    """Bungkus teks dengan kode SGR; transparan bila warna dimatikan."""
    if not codes or not _color():
        return text
    return f"\x1b[{';'.join(str(c) for c in codes)}m{text}\x1b[0m"


def _fg(text: str, code: int, inside_bg: bool = False) -> str:
    """Warnai foreground saja. inside_bg=True → reset hanya fg (pertahankan latar)."""
    if not code or not _color():
        return text
    reset = "\x1b[39m" if inside_bg else "\x1b[0m"
    return f"\x1b[38;5;{code}m{text}{reset}"


def disp_width(s: str) -> int:
    """Lebar tampilan perkiraan: wide CJK/emoji = 2, combining = 0."""
    import unicodedata

    return sum(
        0
        if unicodedata.combining(ch)
        else (2 if unicodedata.east_asian_width(ch) in _WIDE else 1)
        for ch in s
    )


def _truncate(s: str, width: int) -> str:
    """Potong teks ke `width` kolom, elipsis termasuk dalam anggaran."""
    if width <= 0:
        return ""
    if disp_width(s) <= width:
        return s
    import unicodedata

    out: list[str] = []
    n = 0
    for ch in s:
        if unicodedata.combining(ch):
            continue
        w = 2 if unicodedata.east_asian_width(ch) in _WIDE else 1
        if n + w + 1 > width:  # sisakan 1 kolom untuk elipsis
            break
        out.append(ch)
        n += w
    return "".join(out) + "…"


def _fit(s: str, width: int) -> str:
    """Potong + padding spasi ke lebar tampilan persis `width`."""
    s = _truncate(s, max(width, 0))
    return s + " " * max(width - disp_width(s), 0)


def _row(segs: list[tuple[str, int, int]], width: int, selected: bool) -> str:
    """Rakit satu baris daftar selebar `width` (lihat _segment_width)."""
    parts: list[str] = []
    for text, w, fg in segs:
        t = _fit(text, w)
        parts.append(_fg(t, fg, inside_bg=selected) if fg else t)
    return _fill("".join(parts), width, selected)


def _segment_width(segs: list[tuple[str, int, int]]) -> int:
    """Lebar total per-desain segmen (semua lebar kolom tuntas, tanpa sisa)."""
    return sum(w for _, w, _ in segs)


def _fill(body: str, width: int, selected: bool) -> str:
    """Rata-kanan ke `width` dengan latar baris terpilih."""
    pad = max(width - disp_width(body), 0)
    if selected:
        return f"\x1b[48;5;{C_SEL_BG}m\x1b[38;5;{C_SEL_FG}m{body}{' ' * pad}\x1b[0m"
    return body + " " * pad


def _spinner() -> str:
    return _SPINNER[int(time.time() * 4) % len(_SPINNER)]


def read_key() -> str:
    """Baca satu tombol; kembalikan nama kunci atau karakter."""
    if _WINDOWS:
        ch = msvcrt.getwch()  # type: ignore[attr-defined]
        if ch in ("\x00", "\xe0"):  # tombol khusus (panah, F1..)
            ch2 = msvcrt.getwch()  # type: ignore[attr-defined]
            return {"H": UP, "P": DOWN, "K": LEFT, "M": RIGHT}.get(ch2, "")
        return {"\r": ENTER, "\x1b": ESC, "\x08": BACKSPACE}.get(ch, ch)
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)  # type: ignore[attr-defined]
    try:
        tty.setraw(fd)  # type: ignore[attr-defined]
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            seq = ch + sys.stdin.read(2)
            return {"\x1b[A": UP, "\x1b[B": DOWN, "\x1b[C": RIGHT, "\x1b[D": LEFT, "\x1b\x1b\x1b": ESC}.get(seq, ESC)
        return {"\r": ENTER, "\n": ENTER, "\x7f": BACKSPACE}.get(ch, ch)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)  # type: ignore[attr-defined]


@dataclass
class State:
    query: str = ""
    dirty: bool = False  # query diubah sejak search terakhir
    results: list[Track] = field(default_factory=list)
    selected: int = 0
    scroll: int = 0
    status: str = "Ketik query + Enter untuk mencari."
    playing: str | None = None  # judul lagu yang sedang diputar
    paused: bool = False
    volume: int = 100  # volume mpv yang diingat antar trek dan antar sesi (0-130)
    volume_control: bool = False  # True bila mpv IPC aktif (volume/seek bisa dipakai)
    mpv_msg: str | None = None  # alasan IPC mpv gagal (None bila mpv tak dipakai / OK)
    history: list[HistoryEntry] = field(default_factory=list)  # riwayat putar (cache 30 hari / 30 lagu)
    show_history: bool = False  # panel riwayat aktif (Ctrl+R): ↑↓ + Enter putar, x hapus, C bersihkan
    show_queue: bool = False  # panel antrean aktif (Ctrl+Q): ↑↓ + Enter/x/u/d/S/L
    queue: list[Track] = field(default_factory=list)  # antrean aktif: daftar asal putar
    queue_pos: int = 0  # indeks lagu yang sedang diputar di queue
    shuffle: bool = False  # acak lagu berikut (tombol s saat memutar)
    repeat: RepeatMode = "off"  # mode ulangi (tombol r saat memutar)
    settings_path: str | None = None  # lokasi settings.json (diisi run_tui; None = default)
    position: float | None = None  # detik berjalan (mpv time-pos)
    duration: float | None = None  # durasi detik (mpv duration)
    loading: bool = False  # True saat search/resolve berjalan → tampilkan spinner
    last_frame: str = ""  # frame render sebelumnya (anti-kedip: skip bila sama)
    term_title: str = ""  # judul terminal (tracking agar tak ditulis berulang)


def handle_key(st: State, key: str) -> str:
    """Logika murni: ubah state dari satu tombol. Kembalikan aksi: '', 'search', 'play', 'queueplay', 'history', 'queue', 'qadd', 'qdel', 'qmoveup', 'qmovedown', 'qsave', 'qload', 'hdel', 'hclear', 'quit'."""
    if key == CTRL_R:
        return "history"
    if key == CTRL_Q:
        return "queue"
    if key == CTRL_A:
        return "qadd"
    if key == ESC or key == "\x03":
        if st.show_history or st.show_queue:
            st.show_history = False
            st.show_queue = False
            st.selected = 0
            st.scroll = 0
            return ""
        return "quit"
    if st.show_queue and len(key) == 1:
        op = key.lower()
        if op == "x":
            return "qdel"
        if op == "u":
            return "qmoveup"
        if op == "d":
            return "qmovedown"
        if op == "s":
            return "qsave"
        if op == "l":
            return "qload"
    if st.show_history and len(key) == 1:
        op = key.lower()
        if op == "x":
            return "hdel"
        if op == "c":
            return "hclear"
    if st.show_history:
        n = len(st.history)
    elif st.show_queue:
        n = len(st.queue)
    else:
        n = len(st.results)
    if key == UP and n:
        st.selected = (st.selected - 1) % n
        clamp_scroll(st)
        return ""
    if key == DOWN and n:
        st.selected = (st.selected + 1) % n
        clamp_scroll(st)
        return ""
    if key == ENTER:
        if st.show_history and st.history:
            return "play"
        if st.show_queue and st.queue:
            return "queueplay"
        if st.dirty or not st.results:
            return "search"
        return "play"
    if key == BACKSPACE:
        st.show_history = False
        st.show_queue = False
        st.selected = 0
        st.scroll = 0
        st.query = st.query[:-1]
        st.dirty = True
        return ""
    if len(key) == 1 and key.isprintable():
        st.show_history = False
        st.show_queue = False
        st.selected = 0
        st.scroll = 0
        st.query += key
        st.dirty = True
        return ""
    return ""

def volume_bar(vol: int, width: int = 12) -> str:
    """Bar volume murni: 75 → '[███████░░░░░] 75'. Nilai dijepit 0-100 untuk bar."""
    fill = round(min(max(vol, 0), 100) / 100 * width)
    return f"[{'█' * fill}{'░' * (width - fill)}] {vol}"


def progress_bar(pos: float | None, dur: float | None, width: int = 20) -> str:
    """Bar progres murni: (75, 180) → '[████████░░░░░░░░░░░░] 1:15 / 3:00'. Tanpa durasi → ''."""
    if pos is None or dur is None or dur <= 0:
        return ""
    from .player import format_duration

    ratio = min(max(pos / dur, 0.0), 1.0)
    fill = round(ratio * width)
    return f"[{'█' * fill}{'░' * (width - fill)}] {format_duration(pos) or '?'} / {format_duration(dur) or '?'}"


def build_queue(st: State) -> tuple[list[Track], int]:
    """Antrean murni dari daftar aktif (riwayat/history atau hasil search). Kembalikan (queue, pos)."""
    if st.show_history:
        q = [
            Track(video_id=e.video_id, title=e.title, artists=e.artists, duration=e.duration, album=e.album)
            for e in st.history
        ]
    else:
        q = list(st.results)
    pos = min(st.selected, len(q) - 1) if q else 0
    return q, pos


def cycle_repeat(mode: RepeatMode) -> RepeatMode:
    """Murni: putar mode ulangi mati → semua → satu → mati."""
    if mode == "all":
        return "one"
    if mode == "one":
        return "off"
    return "all"


def step_queue(pos: int, length: int, nav: str | None, shuffle: bool, repeat: RepeatMode) -> int | None:
    """Murni (kecuali acak): posisi antrean berikut. None = keluar loop putar.

    nav: 'next' (tombol n) | 'prev' (tombol p) | None (lagu habis alami → autoplay)."""
    if length <= 0 or not 0 <= pos < length:
        return None
    if nav == "prev":
        if repeat == "one":
            return pos
        if pos > 0:
            return pos - 1
        return length - 1 if repeat == "all" else None
    if repeat == "one":  # 'next' manual maupun habis alami → ulangi lagu ini
        return pos
    if shuffle:
        if length == 1:
            return pos
        pick = random.randrange(length - 1)
        return pick + (1 if pick >= pos else 0)  # acak kecuali posisi kini
    if pos + 1 < length:
        return pos + 1
    return 0 if repeat == "all" else None


def _term_size() -> tuple[int, int]:
    """(lebar, tinggi) terminal; fallback 80x24 bila tak terbaca."""
    try:
        w, h = shutil.get_terminal_size()
        return max(int(w), 20), max(int(h), 10)
    except Exception:
        return 80, 24


def _list_rows() -> int:
    _, h = _term_size()
    # ruang untuk: header 1 + pemisah 1 + now-playing 4 + pemisah 1 + footer 3
    return max(min(h - 10, 20), 3)


def _list_width() -> int:
    w, _ = _term_size()
    return max(w - 2, 20)  # margin 1 kolom di kiri/kanan


def clamp_scroll(st: State) -> None:
    """Jaga selected tetap di dalam jendela scroll. Dipanggil saat selected/results berubah."""
    rows = _list_rows()
    if st.selected < st.scroll:
        st.scroll = st.selected
    elif st.selected >= st.scroll + rows:
        st.scroll = st.selected - rows + 1


def _playing_rows(st: State, width: int) -> list[str]:
    """Blok 'Sedang Memutar' dua baris + tag + hint baris, selebar `width`."""
    # baris 1: ▶ judul (cyan) + ⏸ bila jeda + [i/N]
    mark = paint("⏸", C_WARN) if st.paused else paint("▶", C_ACCENT)
    tags = []
    if st.shuffle:
        tags.append("acak")
    if st.repeat == "all":
        tags.append("ulangi-semua")
    elif st.repeat == "one":
        tags.append("ulangi-1")
    tag = paint("[" + "+".join(tags) + "]", C_OK) if tags else ""
    qinfo = ""
    if st.queue:
        cur = min(st.queue_pos, len(st.queue) - 1) if st.queue_pos >= 0 else 0
        qinfo = paint(f"[{cur + 1}/{len(st.queue)}]", C_ARTIST)
    title = st.playing or ""
    # ruang untuk mark + qinfo + tag (+ pemisahnya)
    used = 2 + (len(qinfo) + 1 if qinfo else 0) + (disp_width(tag) + 1 if tag else 0)
    title_w = max(width - used, 8)
    line = f"{mark} {_fg(_truncate(title, title_w), C_TITLE)}"
    if qinfo:
        line += f" {qinfo}"
    if tag:
        line += f" {tag}"
    out = [line]

    # baris 2: bar progres (atau posisi statis) + volume
    bar = progress_bar(st.position, st.duration)
    vol = ""
    if st.volume_control:
        vol = paint(f"♪ {volume_bar(st.volume)}", C_ARTIST)
    if bar and vol:
        gap = max(width - (disp_width(bar) + 1 + disp_width(vol)), 1)
        out.append(f"{bar}{' ' * gap}{vol}")
    elif bar:
        out.append(bar)
    elif vol:
        out.append(" " + vol)
    return out


def _list_title(title: str, count: int, width: int, hint: str) -> str:
    """Judul panel + jumlah + hint kanan, dipotong rapi selebar `width`."""
    left = f"{title} ({count})"
    right = f"{hint}"
    rw = disp_width(right)
    if rw + 2 < width:
        gap = width - disp_width(left) - rw
        if gap >= 2:
            return f"{paint(left, C_TITLE)} {paint('·' * (gap - 2), C_DIM)} {paint(right, C_DIM)}"
    return paint(_truncate(left, width), C_TITLE)


def render(st: State) -> str:
    width = _list_width()
    rows = _list_rows()
    out: list[str] = []

    # ── Header: search bar ──
    prompt = paint("Cari", C_DIM)
    q = _truncate(st.query, max(width - disp_width(prompt) - 4, 4))
    out.append(f"{prompt} › {paint(q, C_TITLE)}{paint('█', C_ACCENT)}")

    # ── Body: now-playing di atas, lalu daftar ──
    if st.playing:
        out.append("")
        out.extend(_playing_rows(st, width))
        out.append("")
        hint1 = "spasi=jeda · n/p=next/prev · s=acak · r=ulangi · x=hapus · q=berhenti"
        hint2 = (
            "-/+=volume · ←→=seek 5 dtk"
            if st.volume_control
            else ("volume/seek gagal — putar ulang" if st.mpv_msg else "volume/seek butuh mpv")
        )
        out.append(paint("  " + _truncate(hint1, max(width - 2, 4)), C_DIM))
        out.append(paint("  " + _truncate(hint2, max(width - 2, 4)), C_DIM))
    if st.loading:
        out.append(paint(f"  {_spinner()} memuat…", C_ACCENT))
    out.append(paint("─" * width, C_DIM))

    def _render_list(items, fmt, sel_idx: int) -> None:
        for i, item in enumerate(items[st.scroll : st.scroll + rows], start=st.scroll):
            segs = fmt(i + 1, item, width)
            out.append(_row(segs, _segment_width(segs), i == sel_idx))

    if st.show_queue:
        if st.queue:
            out.append(
                _list_title("Antrean", len(st.queue), width, "Enter putar · x hapus · u/d susun · S simpan · L muat")
            )
            _render_list(st.queue, _queue_segments, st.selected)
        else:
            out.append(paint("Antrean kosong — Ctrl+A tambah dari hasil, L muat simpanan.", C_DIM))
    elif st.show_history:
        out.append(_list_title("Riwayat", len(st.history), width, "Enter putar · x hapus · C bersihkan"))
        if st.history:
            _render_list(st.history, _history_segments, st.selected)
    elif st.results:
        _render_list(st.results, _result_segments, st.selected)
    else:
        out.append(paint("Belum ada hasil — ketik lagu/artis + Enter, atau Ctrl+R untuk riwayat.", C_DIM))

    # ── Footer: status + tombol ──
    out.append(paint("─" * width, C_DIM))
    status = st.status or ""
    scode = C_ERR if status.startswith(("Gagal", "Riwayat gagal")) else (C_OK if status else C_DIM)
    status_line = _truncate(status, max(width - 2, 4))
    out.append(f"{_fg(status_line, scode)}")
    footer1 = "↑↓ pilih · Enter cari/putar · Esc keluar"
    footer2 = "Ctrl+R riwayat · Ctrl+Q antrean · Ctrl+A tambah"
    out.append(paint("  " + _truncate(footer1, max(width - 2, 4)), C_DIM))
    out.append(paint("  " + _truncate(footer2, max(width - 2, 4)), C_DIM))
    return "\n".join(out)


def _result_segments(i: int, t: Track, width: int) -> list[tuple[str, int, int]]:
    """Kolom baris hasil search: nomor, judul+artis, durasi, videoId (teks polos)."""
    spare = width - (3 + 1 + 5 + 1 + 11)
    return [
        (f"{i:>2}", 3, C_DIM),
        (f"{t.title} — {t.artists}", max(spare, 10), 0),
        (t.duration or "?", 5, C_ARTIST),
        (t.video_id, 11, C_DIM),
    ]


def _queue_segments(i: int, t: Track, width: int) -> list[tuple[str, int, int]]:
    spare = width - (3 + 1 + 5)
    return [
        (f"{i:>2}", 3, C_DIM),
        (f"{t.title} — {t.artists}", max(spare, 10), 0),
        (t.duration or "?", 5, C_ARTIST),
    ]


def _history_segments(i: int, e: HistoryEntry, width: int) -> list[tuple[str, int, int]]:
    spare = width - (3 + 1 + 5 + 1 + 16)
    return [
        (f"{i:>2}", 3, C_DIM),
        (f"{e.title} — {e.artists}", max(spare, 10), 0),
        (e.duration or "?", 5, C_ARTIST),
        (format_played_at(e.played_at), 16, C_DIM),
    ]



def _current_track(st: State) -> Track:
    """Lagu terpilih dari daftar aktif: riwayat (mode Ctrl+R) atau hasil search."""
    if st.show_history:
        e = st.history[st.selected]
        return Track(
            video_id=e.video_id, title=e.title, artists=e.artists, duration=e.duration, album=e.album
        )
    return st.results[st.selected]


def do_history(st: State) -> None:
    """Toggle panel riwayat (Ctrl+R): muat ulang cache, siap ↑↓ + Enter putar."""
    if st.show_history:
        st.show_history = False
        st.selected = 0
        st.scroll = 0
        st.status = "Kembali ke hasil pencarian."
        return
    st.show_queue = False
    try:
        st.history = load_history()
    except Exception as e:
        st.status = f"Riwayat gagal dibaca: {e}"
        return
    if not st.history:
        st.status = "Belum ada riwayat."
        return
    st.show_history = True
    st.selected = 0
    st.scroll = 0
    st.status = f"{len(st.history)} lagu di riwayat — ↑↓ pilih, Enter putar, x hapus, C bersihkan."


def do_history_delete(st: State, path: str | Path | None = None) -> None:
    """Hapus entri riwayat terpilih (x di panel Ctrl+R). Panel tetap terbuka."""
    if not st.show_history or not st.history or not 0 <= st.selected < len(st.history):
        return
    try:
        entries, gone = remove_history_at(st.selected, path)
    except Exception as e:
        st.status = f"Gagal menghapus riwayat: {e}"
        return
    if gone is None:
        return
    st.history = entries
    st.selected = min(st.selected, len(st.history) - 1) if st.history else 0
    clamp_scroll(st)
    st.status = f"Dihapus dari riwayat: {gone.title}."


def do_history_clear(st: State, path: str | Path | None = None) -> None:
    """Bersihkan seluruh riwayat (C di panel Ctrl+R). Panel tetap terbuka."""
    try:
        clear_history(path)
    except Exception as e:
        st.status = f"Gagal menghapus riwayat: {e}"
        return
    st.history = []
    st.selected = 0
    st.scroll = 0
    st.status = "Riwayat dibersihkan."


def do_queue(st: State) -> None:
    """Toggle panel antrean (Ctrl+Q): ↑↓ pilih, Enter putar dari posisi, x/u/d/S/L kelola."""
    if st.show_queue:
        st.show_queue = False
        st.selected = 0
        st.scroll = 0
        st.status = "Kembali ke hasil pencarian."
        return
    st.show_history = False
    st.show_queue = True
    st.selected = min(st.queue_pos, len(st.queue) - 1) if st.queue else 0
    st.scroll = 0
    clamp_scroll(st)
    st.status = (
        f"{len(st.queue)} lagu di antrean — ↑↓ pilih, Enter putar."
        if st.queue
        else "Antrean kosong — tambah dari hasil (Ctrl+A) atau muat simpanan (L)."
    )


def do_queue_add(st: State) -> None:
    """Tambah lagu terpilih (hasil/riwayat) ke ekor antrean (Ctrl+A)."""
    if st.show_queue:
        st.status = "Tutup panel antrean dulu, pilih lagu di hasil/riwayat, lalu Ctrl+A."
        return
    try:
        track = _current_track(st)
    except IndexError:
        st.status = "Tak ada lagu terpilih."
        return
    st.queue.append(track)
    st.status = f"Ditambah ke antrean [{len(st.queue)}]: {track.title}."


def do_queue_delete(st: State) -> None:
    """Hapus entri terpilih dari antrean (x di panel). Panel tetap terbuka."""
    if not st.show_queue or not st.queue or not 0 <= st.selected < len(st.queue):
        return
    gone = st.queue.pop(st.selected)
    st.selected = min(st.selected, len(st.queue) - 1) if st.queue else 0
    clamp_scroll(st)
    st.status = f"Dihapus dari antrean: {gone.title}."


def do_queue_move(st: State, delta: int) -> None:
    """Geser entri terpilih naik (delta=-1, tombol u) / turun (+1, tombol d)."""
    if not st.show_queue or not st.queue:
        return
    j = st.selected + delta
    if not 0 <= st.selected < len(st.queue) or not 0 <= j < len(st.queue):
        st.status = "Sudah di paling atas." if delta < 0 else "Sudah di paling bawah."
        return
    st.queue[st.selected], st.queue[j] = st.queue[j], st.queue[st.selected]
    st.selected = j
    clamp_scroll(st)
    st.status = f"Dipindah ke nomor {j + 1}."


def do_queue_save(st: State, path: str | Path | None = None) -> None:
    """Simpan antrean aktif ke queue.json (S di panel)."""
    if not st.queue:
        st.status = "Antrean kosong, tak ada yang disimpan."
        return
    try:
        f = save_queue(st.queue, path)
    except Exception as e:
        st.status = f"Gagal menyimpan antrean: {e}"
        return
    st.status = f"Antrean disimpan ({len(st.queue)} lagu): {f}."


def do_queue_load(st: State, path: str | Path | None = None) -> None:
    """Muat antrean tersimpan, menggantikan antrean aktif (L di panel)."""
    try:
        st.queue = load_queue(path)
    except Exception as e:
        st.status = f"Gagal memuat antrean: {e}"
        return
    st.queue_pos = 0
    st.selected = 0
    st.scroll = 0
    st.status = (
        f"Antrean dimuat ({len(st.queue)} lagu)."
        if st.queue
        else "Tidak ada antrean tersimpan."
    )


def do_search(st: State) -> None:
    st.loading = True
    st.status = f"Mencari '{st.query}' ..."
    _refresh(st)
    try:
        st.results = search_tracks(st.query, limit=10)
    except Exception as e:
        st.status = f"Search gagal: {e}"
        st.loading = False
        return
    st.loading = False
    st.dirty = False
    st.selected = 0
    st.scroll = 0
    st.status = (
        f"{len(st.results)} hasil untuk '{st.query}'."
        if st.results
        else f"Tidak ada hasil untuk '{st.query}'."
    )

def playing_action(key: str) -> str:
    """Aksi tombol saat memutar: 'pause' | 'voldn' | 'volup' | 'seekback' | 'seekfwd' | 'next' | 'prev' | 'shuffle' | 'repeat' | 'remove' | 'stop' | ''."""
    if key == " ":
        return "pause"
    if key == "-":
        return "voldn"
    if key in ("+", "="):
        return "volup"
    if key == LEFT:
        return "seekback"
    if key == RIGHT:
        return "seekfwd"
    if key in ("n", "N"):
        return "next"
    if key in ("p", "P"):
        return "prev"
    if key in ("s", "S"):
        return "shuffle"
    if key in ("r", "R"):
        return "repeat"
    if key in ("x", "X"):
        return "remove"
    if key in ("q", "Q", ESC, "\x03"):
        return "stop"
    return ""


def _toggle_pause(st: State, proc, ipc) -> None:
    if ipc is not None:
        try:
            ipc.command("cycle", "pause")
            st.paused = bool(ipc.get_property("pause"))
            return
        except Exception:
            pass  # IPC gagal → fallback suspend proses
    if st.paused:
        st.paused = not resume_process(proc)
    else:
        st.paused = suspend_process(proc)
def _change_volume(st: State, ipc, delta: int) -> None:
    # Volume = volume milik proses mpv (per-aplikasi, master Windows tak tersentuh).
    # ffplay tidak punya API remote apa pun → butuh mpv.
    # Nilai diingat di state (dipakai trek berikutnya) + settings.json (sesi lain).
    if not st.volume_control or ipc is None:
        if st.mpv_msg:
            st.status = f"Volume gagal: {st.mpv_msg} — putar ulang."
        else:
            st.status = "Volume butuh mpv — install mpv (https://mpv.io), lalu putar ulang."
        return
    try:
        ipc.command("add", "volume", delta)  # return-nya null → baca ulang
        st.volume = int(ipc.get_property("volume"))
        remember_volume(st.volume, st.settings_path)
    except Exception as e:
        st.status = f"Volume gagal: {e}"


def _seek(st: State, ipc, delta: int) -> None:
    # Seek = perintah mpv via IPC; ffplay tak punya remote control.
    if not st.volume_control or ipc is None:
        if st.mpv_msg:
            st.status = f"Seek gagal: {st.mpv_msg} — putar ulang."
        else:
            st.status = "Seek butuh mpv — install mpv (https://mpv.io), lalu putar ulang."
        return
    try:
        ipc.command("seek", delta)
    except Exception as e:
        st.status = f"Seek gagal: {e}"


def _refresh_progress(st: State, ipc) -> None:
    """Tarik time-pos/duration dari mpv IPC bila ada. Gagal = pertahankan nilai lama."""
    if ipc is None:
        return
    try:
        pos = ipc.get_property("time-pos")
        dur = ipc.get_property("duration")
        st.position = float(pos) if pos is not None else None
        st.duration = float(dur) if dur is not None else None
    except Exception:
        pass


def _term_title(title: str, st: State) -> None:
    """Tulis judul window terminal (sekali per perubahan)."""
    if not title or title == st.term_title:
        return
    st.term_title = title
    if _color():
        sys.stdout.write(f"\x1b]2;{title}\x07")
        sys.stdout.flush()


def _print_frame(st: State, body: str) -> None:
    """Cetak layar penuh sekali; lewati bila isinya tak berubah (anti-kedip)."""
    if body == st.last_frame:
        return
    st.last_frame = body
    print(f"\x1b[2J\x1b[H{body}", end="", flush=True)


def _refresh(st: State, term: str = "") -> None:
    """Render + judul terminal + cetak. Satu pintu untuk semua pembaruan layar."""
    _term_title(term, st)
    _print_frame(st, render(st))


def _poll_key(timeout: float) -> str | None:
    """Satu tombol dalam timeout detik; None bila tidak ada. Poll player tiap tick."""
    if _WINDOWS:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if msvcrt.kbhit():  # type: ignore[attr-defined]
                return read_key()
            time.sleep(0.05)
        return None
    import select

    if select.select([sys.stdin], [], [], timeout)[0]:
        return read_key()
    return None


def do_play(st: State, keep_queue: bool = False) -> None:
    """Putar antrean. keep_queue=True (Enter dari panel antrean): pakai st.queue
    dari posisi terpilih, tanpa membangun ulang dari hasil/riwayat."""
    if keep_queue:
        if not st.queue:
            st.status = "Tidak ada lagu untuk diputar."
            return
        st.queue_pos = min(max(st.selected, 0), len(st.queue) - 1)
    else:
        st.queue, st.queue_pos = build_queue(st)
        if not st.queue:
            st.status = "Tidak ada lagu untuk diputar."
            return
    while 0 <= st.queue_pos < len(st.queue):
        track = st.queue[st.queue_pos]
        nxt: Track | None = st.queue[st.queue_pos + 1] if st.queue_pos + 1 < len(st.queue) else None
        if nxt is not None and not st.shuffle and st.repeat != "one":
            try:
                from .player import prefetch_stream

                prefetch_stream(nxt.video_id)  # hangatkan cache selagi lagu berjalan
            except Exception:
                pass
        st.loading = True
        st.status = f"Mengambil stream {track.title} ..."
        _refresh(st)
        try:
            url = resolve_stream_url(track.video_id)
            use_mpv = os.path.basename(player_cmd(url)[0]).lower().startswith("mpv")
            ipc_path = ("\\\\.\\pipe\\ytmusic-%d" % os.getpid()) if use_mpv else None
            if ipc_path and not _WINDOWS:
                ipc_path = tempfile.gettempdir() + "/ytmusic-%d.sock" % os.getpid()  # type: ignore[attr-defined]
            # Volume diingat: teruskan ke mpv lewat --volume agar trek baru
            # LANGSUNG mulai pada volume yang dipilih (bukan 100% sebentar).
            # st.volume dibawa dari trek sebelumnya / settings.json; bila IPC
            # mpv gagal, kita pasang dari --volume saja.
            proc = start_player(url, ipc_path, st.volume)
            ipc = None
            st.mpv_msg = None
            st.volume_control = False
            st.position = None
            st.duration = None
            if ipc_path:  # mpv di platform apa pun → volume per-aplikasi via IPC
                try:
                    ipc = MpvIpc.connect(ipc_path)
                    if ipc is not None:
                        # mpv baru saja mulai: terapkan volume yang diingat
                        # (--volume sudah diatur, tapi ini memastikan bila mpv
                        # mengabaikannya / volume-max berbeda).
                        ipc.set_property("volume", st.volume)
                        st.volume = int(ipc.get_property("volume"))
                        st.volume_control = True
                except Exception as e:
                    ipc = None  # lanjut tanpa volume control
                    st.mpv_msg = str(e) or "IPC mpv tak terbentuk"
        except Exception as e:
            st.loading = False
            st.status = f"Gagal memutar: {e}"
            return
        st.loading = False
        st.playing = f"{track.title} — {track.artists}"
        st.paused = False
        st.status = f"Memutar {track.title} ..."
        try:
            st.history = add_history(track)
            if st.show_history:
                st.selected = 0
                st.scroll = 0
        except Exception:
            pass

        _refresh(st, f"{track.title} — {track.artists} | ytmusic-cli")
        nav: str | None = None  # 'next' | 'prev' | 'stop' | 'removed' | None(=lagu habis → autoplay)
        try:
            while proc.poll() is None:
                _refresh_progress(st, ipc)
                _refresh(st, f"{track.title} — {track.artists} | ytmusic-cli")
                key = _poll_key(1.0)
                if key is None:
                    continue  # tick progres: tidak ada tombol, lagu masih jalan
                action = playing_action(key)
                if action == "pause":
                    _toggle_pause(st, proc, ipc)
                elif action == "voldn":
                    _change_volume(st, ipc, -5)
                elif action == "volup":
                    _change_volume(st, ipc, +5)
                elif action == "seekback":
                    _seek(st, ipc, -5)
                elif action == "seekfwd":
                    _seek(st, ipc, 5)
                elif action == "shuffle":
                    st.shuffle = not st.shuffle
                    st.status = f"Acak: {'nyala' if st.shuffle else 'mati'}."
                elif action == "repeat":
                    st.repeat = cycle_repeat(st.repeat)
                    st.status = {
                        "off": "Ulangi: mati.",
                        "all": "Ulangi: semua lagu.",
                        "one": "Ulangi: lagu ini.",
                    }[st.repeat]
                elif action == "remove":
                    if 0 <= st.queue_pos < len(st.queue):
                        gone = st.queue.pop(st.queue_pos)
                        st.status = f"Dihapus dari antrean: {gone.title}."
                    if not st.queue:
                        nav = "stop"
                    elif st.queue_pos >= len(st.queue):  # yang dihapus lagu terakhir
                        if st.repeat == "all":
                            st.queue_pos = 0
                            nav = "removed"
                        elif st.shuffle and len(st.queue) > 1:
                            pick = step_queue(len(st.queue) - 1, len(st.queue), None, True, "off")
                            st.queue_pos = pick if pick is not None else 0
                            nav = "removed"
                        elif st.repeat == "one":
                            st.queue_pos = len(st.queue) - 1
                            nav = "removed"
                        else:
                            nav = "stop"
                    else:
                        nav = "removed"
                    break
                elif action in ("next", "prev", "stop"):
                    nav = action
                    break
                else:
                    continue  # tombol tak dikenal: abaikan, jangan hentikan lagu
                _refresh(st, f"{track.title} — {track.artists} | ytmusic-cli")
        finally:
            stop_player(proc)
            if ipc is not None:
                try:
                    ipc.close()
                except Exception:
                    pass
        st.playing = None
        st.paused = False
        st.position = None
        st.duration = None
        if nav == "stop":
            st.status = "Berhenti. ↑↓ pilih lagu lain, Enter putar lagi."
            return
        if nav == "removed":
            continue  # antrean sudah disesuaikan saat hapus; lanjut lagu kini
        # next / prev manual maupun lagu habis alami → posisi berikut
        # (acak / ulangi-semua / ulangi-satu dihitung di sini); None = keluar.
        nxt_pos = step_queue(st.queue_pos, len(st.queue), nav, st.shuffle, st.repeat)
        if nxt_pos is None:
            if nav == "prev":
                st.status = "Sudah di lagu pertama."
            else:
                st.status = "Antrean habis. ↑↓ pilih lagu lain, Enter putar lagi."
            return
        st.queue_pos = nxt_pos


def run_tui() -> int:
    st = State()
    try:
        st.volume = int(load_settings(st.settings_path).get("volume", 100))
    except Exception:
        pass
    try:
        st.history = load_history()
    except Exception:
        st.history = []
    try:
        restored = load_queue()
    except Exception:
        restored = []
    if restored:
        st.queue = restored
        st.status = f"Antrean tersimpan dimuat ({len(restored)} lagu) — Ctrl+Q untuk lihat."
    print("\x1b[?25l", end="", flush=True)  # sembunyikan kursor
    try:
        while True:
            _refresh(st, "ytmusic-cli")
            try:
                action = handle_key(st, read_key())
            except KeyboardInterrupt:
                return 0
            if action == "quit":
                return 0
            if action == "history":
                do_history(st)
            elif action == "hdel":
                do_history_delete(st)
            elif action == "hclear":
                do_history_clear(st)
            elif action == "queue":
                do_queue(st)
            elif action == "qadd":
                do_queue_add(st)
            elif action == "qdel":
                do_queue_delete(st)
            elif action == "qmoveup":
                do_queue_move(st, -1)
            elif action == "qmovedown":
                do_queue_move(st, +1)
            elif action == "qsave":
                do_queue_save(st)
            elif action == "qload":
                do_queue_load(st)
            elif action == "queueplay":
                do_play(st, keep_queue=True)
            elif action == "search":
                do_search(st)
            elif action == "play":
                do_play(st)
    finally:
        st.playing = None
        st.last_frame = ""
        _term_title("", st)
        print("\x1b[?25h\x1b[0m", end="", flush=True)  # kembalikan kursor + warna
    return 0
