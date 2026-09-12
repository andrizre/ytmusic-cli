"""TUI stdlib-only untuk ytmusic-cli (Windows + POSIX).

Kontrol: ketik query + Enter = cari | ↑↓ = pilih | Enter = putar |
spasi = jeda/lanjut | -/+ = volume (butuh mpv) | tombol lain saat
memutar = berhenti | Esc / Ctrl-C = keluar.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field

from .models import Track, format_track
from .player import MpvIpc, player_cmd, resolve_stream_url, resume_process, start_player, suspend_process
from .search import search_tracks

try:
    import msvcrt

    _WINDOWS = True
except ImportError:
    _WINDOWS = False

UP, DOWN, ENTER, ESC, BACKSPACE = "up", "down", "enter", "esc", "backspace"


def read_key() -> str:
    """Baca satu tombol; kembalikan nama kunci atau karakter."""
    if _WINDOWS:
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):  # tombol khusus (panah, F1..)
            ch2 = msvcrt.getwch()
            return {"H": UP, "P": DOWN}.get(ch2, "")
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
            return {"\x1b[A": UP, "\x1b[B": DOWN, "\x1b\x1b\x1b": ESC}.get(seq, ESC)
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


def handle_key(st: State, key: str) -> str:
    """Logika murni: ubah state dari satu tombol. Kembalikan aksi: '', 'search', 'play', 'quit'."""
    if key == ESC or key == "\x03":
        return "quit"
    if key == UP and st.results:
        st.selected = (st.selected - 1) % len(st.results)
        return ""
    if key == DOWN and st.results:
        st.selected = (st.selected + 1) % len(st.results)
        return ""
    if key == ENTER:
        if st.dirty or not st.results:
            return "search"
        return "play"
    if key == BACKSPACE:
        st.query = st.query[:-1]
        st.dirty = True
        return ""
    if len(key) == 1 and key.isprintable():
        st.query += key
        st.dirty = True
        return ""
    return ""

def volume_bar(vol: int, width: int = 12) -> str:
    """Bar volume murni: 75 → '[███████░░░░░] 75'. Nilai dijepit 0-100 untuk bar."""
    fill = round(min(max(vol, 0), 100) / 100 * width)
    return f"[{'█' * fill}{'░' * (width - fill)}] {vol}"


def render(st: State) -> str:
    rows = min(shutil.get_terminal_size().lines - 4, 20)
    out = [f"> Cari: {st.query}█", "─" * 40]
    if st.playing:
        flags = " ⏸ JEDA" if st.paused else ""
        out.append(f"♫ Memutar:{flags} {st.playing}")
        if st.volume is not None:
            out.append(f"Volume: {volume_bar(st.volume)}   ( - / + )")
        out.append("(spasi=jeda | -/+=volume | tombol lain=berhenti)")
    elif not st.results:
        out.append("(belum ada hasil)")
    else:
        if st.selected < st.scroll:
            st.scroll = st.selected
        elif st.selected >= st.scroll + rows:
            st.scroll = st.selected - rows + 1
        for i, t in enumerate(st.results[st.scroll : st.scroll + rows], start=st.scroll):
            line = format_track(i + 1, t)
            out.append(f"\x1b[7m{line}\x1b[0m" if i == st.selected else line)
    out.append("─" * 40)
    out.append(f"{st.status}  [↑↓ pilih | Enter cari/putar | Esc keluar]")
    return "\n".join(out)


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
    """Aksi tombol saat memutar: 'pause' | 'voldn' | 'volup' | 'stop'."""
    if key == " ":
        return "pause"
    if key == "-":
        return "voldn"
    if key in ("+", "="):
        return "volup"
    return "stop"


def _wait_key() -> str:
    """Blokir hingga satu tombol ditekan (player dipoll oleh caller)."""
    if _WINDOWS:
        while not msvcrt.kbhit():
            time.sleep(0.05)
        return msvcrt.getwch()
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
        st.status = "Volume butuh mpv — install mpv (https://mpv.io), lalu putar ulang."
        return
    try:
        st.volume = int(ipc.command("add", "volume", delta))
    except Exception as e:
        st.status = f"Volume gagal: {e}"


def do_play(st: State) -> None:
    track = st.results[st.selected]
    st.status = f"Mengambil stream {track.title} ..."
    print(f"\x1b[2J\x1b[H{render(st)}", end="", flush=True)
    try:
        url = resolve_stream_url(track.video_id)
        use_mpv = player_cmd(url)[0] == "mpv"
        ipc_path = (r"\\.\pipe\ytmusic-%d" % os.getpid()) if use_mpv else None
        if ipc_path and not _WINDOWS:
            ipc_path = tempfile.gettempdir() + "/ytmusic-%d.sock" % os.getpid()
        proc = start_player(url, ipc_path)
        ipc = None
        st.volume = None
        if ipc_path:  # mpv di platform apa pun → volume per-aplikasi via IPC
            try:
                ipc = MpvIpc.connect(ipc_path)
                st.volume = int(ipc.get_property("volume"))
            except Exception:
                ipc = None  # lanjut tanpa volume control
    except Exception as e:
        st.status = f"Gagal memutar: {e}"
        return
    st.playing = f"{track.title} — {track.artists}"
    st.paused = False
    st.status = f"Memutar {track.title} ..."
    print(f"\x1b[2J\x1b[H{render(st)}", end="", flush=True)
    while proc.poll() is None:
        key = _wait_key()
        action = playing_action(key)
        if action == "pause":
            _toggle_pause(st, proc, ipc)
        elif action == "voldn":
            _change_volume(st, ipc, -5)
        elif action == "volup":
            _change_volume(st, ipc, +5)
        else:
            break
        print(f"\x1b[2J\x1b[H{render(st)}", end="", flush=True)
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    st.playing = None
    st.paused = False
    st.status = "Berhenti. ↑↓ pilih lagu lain, Enter putar lagi."


def run_tui() -> int:
    st = State()
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
            if action == "search":
                print(f"\x1b[2J\x1b[H{render(st)}", end="", flush=True)
                do_search(st)
            elif action == "play":
                do_play(st)
    finally:
        print("\x1b[?25h", end="", flush=True)  # kembalikan kursor
    return 0
