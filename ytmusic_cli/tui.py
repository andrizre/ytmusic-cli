"""TUI stdlib-only untuk ytmusic-cli (Windows + POSIX).

Kontrol: ketik query + Enter = cari | ↑↓ = pilih | Enter = putar |
Ctrl+R = riwayat (↑↓ pilih, Enter putar) | spasi = jeda/lanjut |
-/+ = volume (butuh mpv) | ←/→ = seek -5/+5 dtk (butuh mpv) |
q / Esc = berhenti | Esc di daftar = keluar.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field

from .models import Track, format_track
from .history import HistoryEntry, add_history, format_history_entry, load_history
from .player import MpvIpc, player_cmd, resolve_stream_url, resume_process, start_player, stop_player, suspend_process
from .search import search_tracks

try:
    import msvcrt

    _WINDOWS = True
except ImportError:
    _WINDOWS = False

LEFT, RIGHT, UP, DOWN, ENTER, ESC, BACKSPACE = "left", "right", "up", "down", "enter", "esc", "backspace"
CTRL_R = "\x12"  # Ctrl+R = tampilkan/sembunyikan panel riwayat


def read_key() -> str:
    """Baca satu tombol; kembalikan nama kunci atau karakter."""
    if _WINDOWS:
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):  # tombol khusus (panah, F1..)
            ch2 = msvcrt.getwch()
            return {"H": UP, "P": DOWN, "K": LEFT, "M": RIGHT}.get(ch2, "")
        return {"\r": ENTER, "\x1b": ESC, "\x08": BACKSPACE}.get(ch, ch)
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            seq = ch + sys.stdin.read(2)
            return {"\x1b[A": UP, "\x1b[B": DOWN, "\x1b[C": RIGHT, "\x1b[D": LEFT, "\x1b\x1b\x1b": ESC}.get(seq, ESC)
        return {"\r": ENTER, "\n": ENTER, "\x7f": BACKSPACE}.get(ch, ch)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


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
    volume: int | None = None  # 0-130 (mpv) atau None bila tak diketahui
    mpv_msg: str | None = None  # alasan IPC mpv gagal (None bila mpv tak dipakai / OK)
    history: list[HistoryEntry] = field(default_factory=list)  # riwayat putar (cache 30 hari / 30 lagu)
    show_history: bool = False  # panel riwayat aktif (Ctrl+R): ↑↓ + Enter putar


def handle_key(st: State, key: str) -> str:
    """Logika murni: ubah state dari satu tombol. Kembalikan aksi: '', 'search', 'play', 'history', 'quit'."""
    if key == CTRL_R:
        return "history"
    if key == ESC or key == "\x03":
        if st.show_history:
            st.show_history = False
            st.selected = 0
            st.scroll = 0
            return ""
        return "quit"
    n = len(st.history) if st.show_history else len(st.results)
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
        if st.dirty or not st.results:
            return "search"
        return "play"
    if key == BACKSPACE:
        st.show_history = False
        st.selected = 0
        st.scroll = 0
        st.query = st.query[:-1]
        st.dirty = True
        return ""
    if len(key) == 1 and key.isprintable():
        st.show_history = False
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


def _list_rows() -> int:
    return min(shutil.get_terminal_size().lines - 4, 20)


def clamp_scroll(st: State) -> None:
    """Jaga selected tetap di dalam jendela scroll. Dipanggil saat selected/results berubah."""
    rows = _list_rows()
    if st.selected < st.scroll:
        st.scroll = st.selected
    elif st.selected >= st.scroll + rows:
        st.scroll = st.selected - rows + 1


def render(st: State) -> str:
    rows = _list_rows()
    out = [f"> Cari: {st.query}█", "─" * 40]
    if st.playing:
        flags = " ⏸ JEDA" if st.paused else ""
        out.append(f"♫ Memutar:{flags} {st.playing}")
        if st.volume is not None:
            out.append(f"Volume: {volume_bar(st.volume)}   ( - / + | ←/→ seek )")
            out.append("(spasi=jeda | -/+=volume | ←/→=seek 5 dtk | q=berhenti)")
        elif st.mpv_msg:
            out.append("(spasi=jeda | q=berhenti — volume/seek gagal, putar ulang)")
        else:
            out.append("(spasi=jeda | q=berhenti — volume/seek butuh mpv)")
    elif st.show_history:
        out.append(f"Riwayat terakhir ({len(st.history)}) — ↑↓ pilih, Enter putar:")
        for i, h in enumerate(st.history[st.scroll : st.scroll + rows], start=st.scroll):
            line = format_history_entry(i + 1, h)
            out.append(f"\x1b[7m{line}\x1b[0m" if i == st.selected else line)
    elif not st.results:
        out.append("(belum ada hasil — Ctrl+R untuk riwayat)")
    else:
        for i, t in enumerate(st.results[st.scroll : st.scroll + rows], start=st.scroll):
            line = format_track(i + 1, t)
            out.append(f"\x1b[7m{line}\x1b[0m" if i == st.selected else line)
    out.append("─" * 40)
    out.append(f"{st.status}  [↑↓ pilih | Enter cari/putar | Ctrl+R riwayat | Esc keluar]")
    return "\n".join(out)


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
    st.status = f"{len(st.history)} lagu di riwayat — ↑↓ pilih, Enter putar."


def do_search(st: State) -> None:
    st.status = f"Mencari '{st.query}' ..."
    try:
        st.results = search_tracks(st.query, limit=10)
    except Exception as e:
        st.status = f"Search gagal: {e}"
        return
    st.dirty = False
    st.selected = 0
    st.scroll = 0
    st.status = (
        f"{len(st.results)} hasil untuk '{st.query}'."
        if st.results
        else f"Tidak ada hasil untuk '{st.query}'."
    )

def playing_action(key: str) -> str:
    """Aksi tombol saat memutar: 'pause' | 'voldn' | 'volup' | 'seekback' | 'seekfwd' | 'stop' | ''."""
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
    if key in ("q", "Q", ESC, "\x03"):
        return "stop"
    return ""


def _wait_key() -> str:
    """Blokir hingga satu tombol ditekan (player dipoll oleh caller)."""
    if _WINDOWS:
        while not msvcrt.kbhit():
            time.sleep(0.05)
        return read_key()
    import select

    while not select.select([sys.stdin], [], [], 0.2)[0]:
        pass
    return read_key()


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
    if ipc is None:
        if st.mpv_msg:
            st.status = f"Volume gagal: {st.mpv_msg} — putar ulang."
        else:
            st.status = "Volume butuh mpv — install mpv (https://mpv.io), lalu putar ulang."
        return
    try:
        ipc.command("add", "volume", delta)  # return-nya null → baca ulang
        st.volume = int(ipc.get_property("volume"))
    except Exception as e:
        st.status = f"Volume gagal: {e}"


def _seek(st: State, ipc, delta: int) -> None:
    # Seek = perintah mpv via IPC; ffplay tak punya remote control.
    if ipc is None:
        if st.mpv_msg:
            st.status = f"Seek gagal: {st.mpv_msg} — putar ulang."
        else:
            st.status = "Seek butuh mpv — install mpv (https://mpv.io), lalu putar ulang."
        return
    try:
        ipc.command("seek", delta)
    except Exception as e:
        st.status = f"Seek gagal: {e}"


def do_play(st: State) -> None:
    track = _current_track(st)
    st.status = f"Mengambil stream {track.title} ..."
    print(f"\x1b[2J\x1b[H{render(st)}", end="", flush=True)
    try:
        url = resolve_stream_url(track.video_id)
        use_mpv = os.path.basename(player_cmd(url)[0]).lower().startswith("mpv")
        ipc_path = (r"\\.\pipe\ytmusic-%d" % os.getpid()) if use_mpv else None
        if ipc_path and not _WINDOWS:
            ipc_path = tempfile.gettempdir() + "/ytmusic-%d.sock" % os.getpid()
        proc = start_player(url, ipc_path)
        ipc = None
        st.volume = None
        st.mpv_msg = None
        if ipc_path:  # mpv di platform apa pun → volume per-aplikasi via IPC
            try:
                ipc = MpvIpc.connect(ipc_path)
                st.volume = int(ipc.get_property("volume"))
            except Exception as e:
                ipc = None  # lanjut tanpa volume control
                st.mpv_msg = str(e) or "IPC mpv tak terbentuk"
    except Exception as e:
        st.status = f"Gagal memutar: {e}"
        return
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

    print(f"\x1b[2J\x1b[H{render(st)}", end="", flush=True)
    try:
        while proc.poll() is None:
            key = _wait_key()
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
            elif action == "stop":
                break
            else:
                continue  # tombol tak dikenal: abaikan, jangan hentikan lagu
            print(f"\x1b[2J\x1b[H{render(st)}", end="", flush=True)
    finally:
        stop_player(proc)
        if ipc is not None:
            try:
                ipc.close()
            except Exception:
                pass
    st.playing = None
    st.paused = False
    st.status = "Berhenti. ↑↓ pilih lagu lain, Enter putar lagi."


def run_tui() -> int:
    st = State()
    try:
        st.history = load_history()
    except Exception:
        st.history = []
    print("\x1b[?25l", end="", flush=True)  # sembunyikan kursor
    try:
        while True:
            print(f"\x1b[2J\x1b[H{render(st)}", end="", flush=True)
            try:
                action = handle_key(st, read_key())
            except KeyboardInterrupt:
                return 0
            if action == "quit":
                return 0
            if action == "history":
                do_history(st)
            elif action == "search":
                print(f"\x1b[2J\x1b[H{render(st)}", end="", flush=True)
                do_search(st)
            elif action == "play":
                do_play(st)
    finally:
        print("\x1b[?25h", end="", flush=True)  # kembalikan kursor
    return 0
