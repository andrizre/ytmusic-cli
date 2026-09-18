"""Riwayat lagu yang diputar, disimpan sebagai cache JSON.

Batas: maksimal 30 lagu terbaru; entri lebih tua dari 30 hari dibuang.
Lokasi: %LOCALAPPDATA%/ytmusic-cli/history.json (Windows) atau
$XDG_CACHE_HOME/ytmusic-cli/history.json (~/.cache fallback di POSIX).
Env YTMUSIC_CLI_HISTORY_FILE menimpa lokasi (untuk tes).
"""

import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .models import Track

MAX_HISTORY = 30
MAX_AGE_DAYS = 30
MAX_AGE_SECONDS = MAX_AGE_DAYS * 24 * 3600


@dataclass(frozen=True)
class HistoryEntry:
    video_id: str
    title: str
    artists: str
    duration: str | None = None
    album: str | None = None
    played_at: float = 0.0


def _cache_dir() -> Path:
    """Direktori cache ytmusic-cli per platform."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / "ytmusic-cli"
    xdg = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(xdg) / "ytmusic-cli"


def history_file(path: str | Path | None = None) -> Path:
    """Lokasi file cache riwayat. Argumen path menimpa default (untuk tes)."""
    if path is not None:
        return Path(path).expanduser()
    override = os.environ.get("YTMUSIC_CLI_HISTORY_FILE")
    if override:
        return Path(override).expanduser()
    return _cache_dir() / "history.json"


def entry_from_dict(d: dict) -> HistoryEntry | None:
    """Parse satu dict JSON; None bila video_id hilang/rusak (toleran)."""
    try:
        vid = str(d.get("video_id") or "")
        if not vid:
            return None
        return HistoryEntry(
            video_id=vid,
            title=str(d.get("title") or vid),
            artists=str(d.get("artists") or "Unknown"),
            duration=d.get("duration"),
            album=d.get("album"),
            played_at=float(d.get("played_at") or 0.0),
        )
    except (TypeError, ValueError):
        return None


def prune_history(
    entries: list[HistoryEntry], now: float | None = None
) -> list[HistoryEntry]:
    """Murni: buang entri >30 hari, dedupe video_id (terbaru dipertahankan), potong 30."""
    now = time.time() if now is None else now
    cutoff = now - MAX_AGE_SECONDS
    seen: set[str] = set()
    out: list[HistoryEntry] = []
    for e in entries:
        if e.played_at < cutoff:
            continue
        if e.video_id in seen:
            continue
        seen.add(e.video_id)
        out.append(e)
        if len(out) >= MAX_HISTORY:
            break
    return out


def load_history(path: str | Path | None = None) -> list[HistoryEntry]:
    """Baca cache; file hilang/rusak → []. Urutan terbaru-dulu, sudah di-prune."""
    try:
        raw = json.loads(history_file(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    entries = [
        e
        for d in raw
        if isinstance(d, dict)
        for e in [entry_from_dict(d)]
        if e is not None
    ]
    return prune_history(entries)


def save_history(
    entries: list[HistoryEntry], path: str | Path | None = None
) -> Path:
    """Tulis cache atomis (tmp + replace). Kembalikan path file."""
    f = history_file(path)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(
        json.dumps([asdict(e) for e in entries], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, f)
    return f


def add_history(
    track: Track,
    played_at: float | None = None,
    path: str | Path | None = None,
) -> list[HistoryEntry]:
    """Catat satu putaran: pindah ke depan, dedupe, prune, simpan. Kembalikan isi baru."""
    now = time.time() if played_at is None else played_at
    new = HistoryEntry(
        video_id=track.video_id,
        title=track.title,
        artists=track.artists,
        duration=track.duration,
        album=track.album,
        played_at=now,
    )
    entries = prune_history([new] + load_history(path), now=now)
    save_history(entries, path)
    return entries


def clear_history(path: str | Path | None = None) -> None:
    """Hapus file cache riwayat (tak ada file = tak ada error)."""
    try:
        history_file(path).unlink()
    except FileNotFoundError:
        pass


def format_played_at(ts: float, now: float | None = None) -> str:
    """'2026-09-12 10:30' + embel 'kemarin' / 'N hari lalu' bila relevan."""
    try:
        s = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return "?"
    now = time.time() if now is None else now
    days = int((now - ts) // 86400)
    if days <= 0:
        return s
    if days == 1:
        return f"{s} (kemarin)"
    return f"{s} ({days} hari lalu)"


def format_history_entry(i: int, e: HistoryEntry) -> str:
    return (
        f"[{i}] {e.title} — {e.artists} ({e.duration or '?'}) "
        f"[{e.video_id}] • {format_played_at(e.played_at)}"
    )


def queue_file(path: str | Path | None = None) -> Path:
    """Lokasi file antrean tersimpan. Env YTMUSIC_CLI_QUEUE_FILE menimpa default."""
    if path is not None:
        return Path(path).expanduser()
    override = os.environ.get("YTMUSIC_CLI_QUEUE_FILE")
    if override:
        return Path(override).expanduser()
    return _cache_dir() / "queue.json"


def _queue_track_from_dict(d: dict) -> Track | None:
    """Parse satu dict antrean; None bila video_id hilang/rusak (toleran)."""
    try:
        vid = str(d.get("video_id") or "")
        if not vid:
            return None
        return Track(
            video_id=vid,
            title=str(d.get("title") or vid),
            artists=str(d.get("artists") or "Unknown"),
            duration=d.get("duration"),
            album=d.get("album"),
        )
    except (TypeError, ValueError):
        return None


def load_queue(path: str | Path | None = None) -> list[Track]:
    """Baca antrean tersimpan; file hilang/rusak → []."""
    try:
        raw = json.loads(queue_file(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    return [
        t
        for d in raw
        if isinstance(d, dict)
        for t in [_queue_track_from_dict(d)]
        if t is not None
    ]


def save_queue(queue: list[Track], path: str | Path | None = None) -> Path:
    """Tulis antrean atomis (tmp + replace). Kembalikan path file."""
    f = queue_file(path)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(
        json.dumps([asdict(e) for e in queue], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, f)
    return f
