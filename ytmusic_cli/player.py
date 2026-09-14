import json
import os
import shutil
import subprocess
import time
import urllib.parse

import yt_dlp

from .models import Track

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


_STREAM_CACHE: dict[str, tuple[str, float]] = {}  # key video/url → (stream_url, valid_sampai)


def _is_video_id(s: str) -> bool:
    return len(s) == 11 and all(c.isalnum() or c in "-_" for c in s)


def normalize_video_id(video_id_or_url: str, url: str | None = None) -> str:
    """Normalisasi input play menjadi videoId 11 char bila bisa ditebak.

    Menangani: id polos, watch?v=, youtu.be/<id>, /shorts|embed|v/<id>.
    Fallback: kembalikan input apa adanya (mis. URL non-standar)."""
    if _is_video_id(video_id_or_url):
        return video_id_or_url
    parsed = urllib.parse.urlsplit(url or video_id_or_url)
    v = urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
    if _is_video_id(v):
        return v
    parts = [p for p in parsed.path.split("/") if p]
    if parts and _is_video_id(parts[-1]):
        return parts[-1]
    return v or video_id_or_url


def _cache_key(video_id_or_url: str, url: str) -> str:
    return normalize_video_id(video_id_or_url, url)


def _watch_url(video_id_or_url: str) -> str:
    if _is_video_id(video_id_or_url):
        return f"https://music.youtube.com/watch?v={video_id_or_url}"
    return video_id_or_url


def format_duration(seconds: float | int | None) -> str | None:
    """Detik → 'm:ss' / 'h:mm:ss'. None/invalid → None."""
    if seconds is None:
        return None
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return None
    if total < 0:
        return None
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _clean_artist(name: str | None) -> str | None:
    if not name:
        return None
    name = name.strip()
    if name.endswith(" - Topic"):
        name = name[: -len(" - Topic")].strip()
    return name or None


def track_from_info(info: dict, fallback_id: str) -> Track:
    """Bangun Track dari info dict yt-dlp. Murni (mudah dites)."""
    vid = info.get("id") or fallback_id
    title = info.get("title") or vid
    artists = (
        info.get("artist")
        or info.get("creator")
        or _clean_artist(info.get("uploader"))
        or info.get("channel")
        or "Unknown"
    )
    return Track(
        video_id=vid,
        title=title,
        artists=artists,
        duration=format_duration(info.get("duration")),
        album=info.get("album"),
    )


def _cache_put(key: str, stream_url: str) -> None:
    # googlevideo URL membawa ?expire=... (umur ~6 jam); cache sampai 60 dtk sebelumnya.
    try:
        exp = float(
            urllib.parse.parse_qs(urllib.parse.urlsplit(stream_url).query).get(
                "expire", [""]
            )[0]
        )
    except ValueError:
        return
    if exp > time.time() + 60:
        if len(_STREAM_CACHE) > 64:
            _STREAM_CACHE.clear()
        _STREAM_CACHE[key] = (stream_url, exp - 60)


def _ydl_opts() -> dict:
    return {
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


def fetch_info(video_id_or_url: str) -> dict:
    """Ambil info dict yt-dlp sekali (dipakai untuk stream URL + metadata)."""
    url = _watch_url(video_id_or_url)
    with yt_dlp.YoutubeDL(_ydl_opts()) as ydl:
        return ydl.extract_info(url, download=False)


def stream_url_from_info(info: dict, key: str) -> str:
    """Pilih stream audio dari info dict + cache. Raise bila tak ada."""
    direct = info.get("url")
    if direct:
        _cache_put(key, direct)
        return direct
    for fmt in info.get("requested_formats") or []:
        if fmt.get("acodec") != "none" and fmt.get("url"):
            _cache_put(key, fmt["url"])
            return fmt["url"]
    raise RuntimeError("tidak ada stream audio")


def resolve_stream_url(video_id_or_url: str) -> str:
    url = _watch_url(video_id_or_url)
    key = _cache_key(video_id_or_url, url)
    hit = _STREAM_CACHE.get(key)
    if hit and hit[1] > time.time():
        return hit[0]
    return stream_url_from_info(fetch_info(video_id_or_url), key)


def resolve_stream_and_track(video_id_or_url: str) -> tuple[str, Track]:
    """Satu fetch yt-dlp → (stream_url, Track). Cache stream tetap dipakai bila hit."""
    url = _watch_url(video_id_or_url)
    key = _cache_key(video_id_or_url, url)
    vid = normalize_video_id(video_id_or_url, url)
    hit = _STREAM_CACHE.get(key)
    if hit and hit[1] > time.time():
        try:
            return hit[0], track_from_info(fetch_info(video_id_or_url), vid)
        except Exception:
            return hit[0], Track(video_id=vid, title=vid, artists="Unknown", duration=None, album=None)
    info = fetch_info(video_id_or_url)
    return stream_url_from_info(info, key), track_from_info(info, vid)


def resolve_track(video_id_or_url: str) -> Track:
    """Metadata lagu untuk riwayat. Fallback: Track minimal agar play tetap tercatat."""
    url = _watch_url(video_id_or_url)
    vid = normalize_video_id(video_id_or_url, url)
    try:
        return resolve_stream_and_track(video_id_or_url)[1]
    except Exception:
        return Track(video_id=vid, title=vid, artists="Unknown", duration=None, album=None)


def _resolve_mpv() -> str | None:
    """Path mpv, diutamakan mpv.exe asli (bukan mpv.com wrapper).

    which("mpv") di Windows menang ke mpv.COM (urutan PATHEXT) — itu hanya
    wrapper console yang melahirkan mpv.exe anak. Luncurkan mpv.exe langsung
    agar job kill-on-close mengikat proses audio yang sebenarnya.
    """
    exe = shutil.which("mpv.exe")
    if exe:
        return exe
    return shutil.which("mpv")


def player_cmd(stream_url: str) -> list[str]:
    mpv = _resolve_mpv()
    if mpv:
        return [mpv, "--no-video", stream_url]
    if shutil.which("ffplay"):
        return ["ffplay", "-nodisp", "-autoexit", stream_url]
    raise RuntimeError(
        "mpv/ffplay tidak ditemukan — install mpv (https://mpv.io)"
        " atau ffmpeg yang berisi ffplay"
    )


def play_stream(stream_url: str) -> int:
    proc = subprocess.Popen(player_cmd(stream_url))
    _attach_kill_on_parent_exit(proc)
    try:
        return proc.wait()
    except KeyboardInterrupt:
        stop_player(proc)
        return 0
    finally:
        stop_player(proc)


def play_track(video_id_or_url: str) -> int:
    return play_stream(resolve_stream_url(video_id_or_url))


def play_with_metadata(video_id_or_url: str) -> tuple[int, Track]:
    """Putar + kembalikan metadata (satu fetch). Raise bila resolve/play gagal."""
    stream_url, track = resolve_stream_and_track(video_id_or_url)
    return play_stream(stream_url), track


JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000


def _attach_kill_on_parent_exit(proc: subprocess.Popen) -> None:
    """Ikat player ke Job Object KILL_ON_JOB_CLOSE (Windows saja).

    Handle job disimpan di proc agar hidup selama player jalan: saat console
    di-X, parent mati → handle tertutup → Windows membunuh player otomatis.
    Bila assign gagal (mis. proses sudah di dalam job), handle job dilepas
    agar tidak bocor; perilaku kembali seperti sebelumnya.
    """
    if os.name != "nt":
        return
    h_job = None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessCount", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", ctypes.c_byte * 48),  # JOBOBJECT_IO_RATE_CONTROL_INFORMATION
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        h_job = kernel32.CreateJobObjectW(None, None)
        if not h_job:
            return
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        # 9 = JobObjectExtendedLimitInformation (struct basic 2 ditolak: ERROR_INVALID_PARAMETER).
        ok = kernel32.SetInformationJobObject(
            h_job, 9, ctypes.byref(info), ctypes.sizeof(info)
        )
        handle = int(getattr(proc, "_handle", 0) or 0)
        if ok and handle:
            ok = kernel32.AssignProcessToJobObject(h_job, handle)
        if ok:
            proc._ytmusic_job = h_job  # noqa: SLF001 — jaga handle tetap terbuka
            h_job = None  # kepemilikan pindah ke proc; jangan ditutup di finally
    except Exception:
        pass
    finally:
        if h_job:
            try:
                import ctypes

                ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(h_job)
            except Exception:
                pass


def stop_player(proc: subprocess.Popen, timeout: float = 5) -> None:
    """Hentikan player + tutup handle job. Aman dipanggil berkali-kali."""
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
    finally:
        h_job = getattr(proc, "_ytmusic_job", None)
        if h_job:
            try:
                import ctypes

                ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(h_job)
            except Exception:
                pass
            finally:
                try:
                    del proc._ytmusic_job  # noqa: SLF001
                except AttributeError:
                    pass


def start_player(stream_url: str, ipc_path: str | None = None) -> subprocess.Popen:
    """Luncurkan player tanpa mewarisi stdio (untuk TUI). Tambah IPC server bila mpv."""
    cmd = player_cmd(stream_url)
    if ipc_path and os.path.basename(cmd[0]).lower().startswith("mpv"):
        cmd = [cmd[0], f"--input-ipc-server={ipc_path}", *cmd[1:]]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _attach_kill_on_parent_exit(proc)
    return proc


def _for_each_thread(pid: int, action) -> bool:
    """Jalankan action(kernel32, handle) untuk tiap thread milik pid. False bila snapshot gagal."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

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

    snap = kernel32.CreateToolhelp32Snapshot(0x00000004, 0)  # TH32CS_SNAPTHREAD
    if snap == wintypes.HANDLE(-1).value:
        return False
    try:
        te = THREADENTRY32()
        te.dwSize = ctypes.sizeof(THREADENTRY32)
        ok = kernel32.Thread32First(snap, ctypes.byref(te))
        while ok:
            if te.th32OwnerProcessID == pid:
                h = kernel32.OpenThread(0x0002, False, te.th32ThreadID)  # THREAD_SUSPEND_RESUME
                if h:
                    try:
                        action(kernel32, h)
                    finally:
                        kernel32.CloseHandle(h)
            ok = kernel32.Thread32Next(snap, ctypes.byref(te))
    finally:
        kernel32.CloseHandle(snap)
    return True


def suspend_process(proc: subprocess.Popen) -> bool:
    """Bekukan proses player (jeda). True bila berhasil."""
    if proc.poll() is not None:
        return False
    if os.name == "posix":
        import signal

        proc.send_signal(signal.SIGSTOP)
        return True
    return _for_each_thread(proc.pid, lambda k, h: k.SuspendThread(h))


def _resume_one(kernel32, h) -> None:
    while kernel32.ResumeThread(h) > 1:
        pass


def resume_process(proc: subprocess.Popen) -> bool:
    """Lanjutkan proses yang dibekukan. True bila berhasil."""
    if proc.poll() is not None:
        return False
    if os.name == "posix":
        import signal

        proc.send_signal(signal.SIGCONT)
        return True
    return _for_each_thread(proc.pid, _resume_one)


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

    def close(self) -> None:
        try:
            self._f.close()
        except Exception:
            pass
