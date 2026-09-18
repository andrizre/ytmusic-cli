import time

from ytmusicapi import YTMusic

from .models import Track


def search_tracks(query: str, limit: int = 10, tries: int = 3) -> list[Track]:
    if not query or not query.strip():
        raise ValueError("query kosong")
    last: Exception | None = None
    for attempt in range(max(tries, 1)):
        try:
            results = YTMusic().search(query, filter="songs", limit=limit)
            tracks: list[Track] = []
            for item in results:
                video_id = item.get("videoId")
                if not video_id:
                    continue
                artists = ", ".join(a["name"] for a in item.get("artists", [])) or "Unknown"
                album = (item.get("album") or {}).get("name")
                tracks.append(
                    Track(
                        video_id=video_id,
                        title=item.get("title", "Unknown"),
                        artists=artists,
                        duration=item.get("duration"),
                        album=album,
                    )
                )
            return tracks
        except Exception as e:
            last = e
            if attempt + 1 >= max(tries, 1):
                raise
            time.sleep(0.5 * (2**attempt))
    raise last or RuntimeError("search gagal")
