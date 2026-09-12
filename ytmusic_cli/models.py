from dataclasses import dataclass


@dataclass(frozen=True)
class Track:
    video_id: str
    title: str
    artists: str
    duration: str | None
    album: str | None


def format_track(i: int, t: Track) -> str:
    return f"[{i}] {t.title} — {t.artists} ({t.duration or '?'}) [{t.video_id}]"
