"""Cek lingkungan ytmusic-cli: player, library, yt-dlp. Murni stdlib."""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def collect() -> list[Check]:
    checks: list[Check] = []
    ok_py = sys.version_info >= (3, 10)
    checks.append(
        Check(
            "python",
            ok_py,
            f"{sys.version.split()[0]} (butuh >=3.10)" if not ok_py else sys.version.split()[0],
        )
    )
    mpv = shutil.which("mpv") or shutil.which("mpv.exe")
    checks.append(Check("mpv", bool(mpv), mpv or "tidak ditemukan — volume/seek TUI butuh mpv (https://mpv.io)"))
    ffplay = shutil.which("ffplay") or shutil.which("ffplay.exe")
    checks.append(
        Check(
            "ffplay",
            bool(ffplay),
            ffplay or "tidak ditemukan — fallback bila mpv absen",
        )
    )
    if not mpv and not ffplay:
        checks.append(Check("player", False, "mpv/ffplay tak ada — install salah satu"))
    else:
        checks.append(Check("player", True, "ok"))
    try:
        from importlib.metadata import version as _pkg_version

        try:
            ver: object = _pkg_version("yt-dlp")
        except Exception:
            import yt_dlp  # type: ignore

            ver = getattr(yt_dlp, "__version__", None) or getattr(yt_dlp.version, "__version__", "?")
        checks.append(Check("yt-dlp", True, str(ver)))
    except Exception as e:
        checks.append(Check("yt-dlp", False, f"import gagal: {e}"))
    try:
        import ytmusicapi  # type: ignore  # noqa: F401

        checks.append(Check("ytmusicapi", True, "ok"))
    except Exception as e:
        checks.append(Check("ytmusicapi", False, f"import gagal: {e}"))
    try:
        from .history import history_file

        p = history_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        checks.append(Check("riwayat", True, str(p)))
    except Exception as e:
        checks.append(Check("riwayat", False, str(e)))
    return checks


def format_report(checks: list[Check]) -> str:
    lines = [f"[{'OK' if c.ok else 'X'}] {c.name}: {c.detail}" for c in checks]
    return "\n".join(lines)


def main() -> int:
    checks = collect()
    print(format_report(checks))
    critical = [c for c in checks if c.name in ("python", "player", "yt-dlp", "ytmusicapi")]
    return 0 if all(c.ok for c in critical) else 1
