import json
import os
import shutil
import subprocess

import yt_dlp

# winget (shinchiro build) menulis ke Program Files tapi sesi terminal lama
# tidak melihat PATH baru → daftarkan lokasi umum agar which("mpv") tetap temu.
if os.name == "nt" and not shutil.which("mpv"):
    _candidates = [
        r"C:\Program Files\MPV Player",
        os.path.join(os.environ.get("ProgramFiles", ""), "MPV Player"),
        os.path.join(os.environ.get("LocalAppData", ""), "Programs", "mpv"),
    ]
    for _d in dict.fromkeys(_candidates):
        if _d and os.path.isfile(os.path.join(_d, "mpv.exe")):
            os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + _d
            break


def resolve_stream_url(video_id_or_url: str) -> str:
    url = video_id_or_url
    if len(video_id_or_url) == 11 and all(
        c.isalnum() or c in "-_" for c in video_id_or_url
    ):
        url = f"https://music.youtube.com/watch?v={video_id_or_url}"
    opts = {
        "format": "bestaudio/best",
        "quiet": True,
        "noplaylist": True,
        "no_warnings": True,
        # HLS (m3u8) / DASH manifest sering timeout & bikin "Mengambil stream"
        # stuck 30-60 dtk; audio langsung (https, itag 251/140) sudah cukup.
        "extractor_args": {"youtube": {"skip": ["hls", "dash"]}},
        "socket_timeout": 15,
        "retries": 2,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    direct = info.get("url")
    if direct:
        return direct
    for fmt in info.get("requested_formats") or []:
        if fmt.get("acodec") != "none" and fmt.get("url"):
            return fmt["url"]
    raise RuntimeError("tidak ada stream audio")


def player_cmd(stream_url: str) -> list[str]:
    if shutil.which("mpv"):
        return ["mpv", "--no-video", stream_url]
    if shutil.which("ffplay"):
        return ["ffplay", "-nodisp", "-autoexit", stream_url]
    raise RuntimeError(
        "mpv/ffplay tidak ditemukan — install mpv (https://mpv.io)"
        " atau ffmpeg yang berisi ffplay"
    )


def play_stream(stream_url: str) -> int:
    try:
        result = subprocess.run(player_cmd(stream_url))
    except KeyboardInterrupt:
        return 0
    return result.returncode


def play_track(video_id_or_url: str) -> int:
    return play_stream(resolve_stream_url(video_id_or_url))

def start_player(stream_url: str, ipc_path: str | None = None) -> subprocess.Popen:
    """Luncurkan player tanpa mewarisi stdio (untuk TUI). Tambah IPC server bila mpv."""
    cmd = player_cmd(stream_url)
    if ipc_path and cmd[0] == "mpv":
        cmd = [cmd[0], f"--input-ipc-server={ipc_path}", *cmd[1:]]
    return subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def suspend_process(proc: subprocess.Popen) -> bool:
    """Bekukan proses player (jeda). True bila berhasil."""
    if proc.poll() is not None:
        return False
    if os.name == "posix":
        import signal

        proc.send_signal(signal.SIGSTOP)
        return True
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    TH32CS_SNAPTHREAD = 0x00000004
    THREAD_SUSPEND_RESUME = 0x0002

    class THREADENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG),
            ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        ]

    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if snap == wintypes.HANDLE(-1).value:
        return False
    try:
        te = THREADENTRY32()
        te.dwSize = ctypes.sizeof(THREADENTRY32)
        ok = kernel32.Thread32First(snap, ctypes.byref(te))
        while ok:
            if te.th32OwnerProcessID == proc.pid:
                h = kernel32.OpenThread(THREAD_SUSPEND_RESUME, False, te.th32ThreadID)
                if h:
                    kernel32.SuspendThread(h)
                    kernel32.CloseHandle(h)
            ok = kernel32.Thread32Next(snap, ctypes.byref(te))
    finally:
        kernel32.CloseHandle(snap)
    return True


def resume_process(proc: subprocess.Popen) -> bool:
    """Lanjutkan proses yang dibekukan. True bila berhasil."""
    if proc.poll() is not None:
        return False
    if os.name == "posix":
        import signal

        proc.send_signal(signal.SIGCONT)
        return True
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    TH32CS_SNAPTHREAD = 0x00000004
    THREAD_SUSPEND_RESUME = 0x0002

    class THREADENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG),
            ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        ]

    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if snap == wintypes.HANDLE(-1).value:
        return False
    try:
        te = THREADENTRY32()
        te.dwSize = ctypes.sizeof(THREADENTRY32)
        ok = kernel32.Thread32First(snap, ctypes.byref(te))
        while ok:
            if te.th32OwnerProcessID == proc.pid:
                h = kernel32.OpenThread(THREAD_SUSPEND_RESUME, False, te.th32ThreadID)
                if h:
                    while kernel32.ResumeThread(h) > 1:
                        pass
                    kernel32.CloseHandle(h)
            ok = kernel32.Thread32Next(snap, ctypes.byref(te))
    finally:
        kernel32.CloseHandle(snap)
    return True


class MpvIpc:
    """Klien JSON IPC mpv di atas koneksi biner apa saja (named pipe / unix socket)."""

    def __init__(self, conn):
        self._f = conn
        self._buf = b""
        self._seq = 0

    @classmethod
    def connect(cls, path: str, timeout: float = 5.0) -> "MpvIpc":
        import socket
        import time

        deadline = time.time() + timeout
        while True:
            try:
                if path.startswith("\\\\.\\pipe\\"):
                    return cls(open(path, "r+b", buffering=0))
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    s.connect(path)
                except Exception:
                    s.close()
                    raise
                return cls(s.makefile("rwb", buffering=0))  # tanpa buffer: read() tidak menelan baris berikut
            except (OSError, FileNotFoundError):
                if time.time() >= deadline:
                    raise RuntimeError(f"mpv IPC tidak tersedia: {path}")
                time.sleep(0.2)

    def command(self, *args):
        self._seq += 1
        rid = self._seq
        self._f.write((json.dumps({"command": list(args), "request_id": rid}) + "\n").encode())
        self._f.flush()
        while True:
            while b"\n" not in self._buf:
                chunk = self._f.read(4096)
                if not chunk:
                    raise RuntimeError("mpv IPC putus")
                self._buf += chunk
            line, self._buf = self._buf.split(b"\n", 1)
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("request_id") == rid:
                if msg.get("error") not in (None, "success"):
                    raise RuntimeError(f"mpv: {msg.get('error')}")
                return msg.get("data")

    def get_property(self, name: str):
        return self.command("get_property", name)

    def set_property(self, name: str, value) -> None:
        self.command("set_property", name, value)
