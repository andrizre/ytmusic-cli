import argparse
import sys

from .models import format_track
from .player import play_with_metadata
from .search import search_tracks


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ytmusic", description="YouTube Music CLI: search + play")
    sub = p.add_subparsers(dest="command")

    ps = sub.add_parser("search", help="Cari lagu")
    ps.add_argument("query", help="Kata kunci pencarian")
    ps.add_argument("-n", "--limit", type=int, default=10, help="Jumlah hasil (default 10)")

    pp = sub.add_parser("play", help="Putar audio dari videoId atau URL")
    pp.add_argument("input", help="videoId (11 char) atau URL music.youtube.com/youtube.com/watchyoutu.be/")
    ph = sub.add_parser("history", help="Lihat riwayat putar (cache 30 hari / 30 lagu)")
    ph.add_argument("-n", "--limit", type=int, default=30, help="Jumlah entri (default 30)")
    ph.add_argument("--clear", action="store_true", help="Hapus riwayat")
    ph.add_argument("--json", action="store_true", help="Output JSON")

    sub.add_parser("tui", help="Mode interaktif (ketik query, pilih, putar)")
    return p


def main(argv: list[str] | None = None) -> int:
    if not argv and len(sys.argv) == 1:
        argv = ["tui"]  # `ytmusic` tanpa argumen langsung buka TUI
    args = build_parser().parse_args(argv)
    if args.command == "tui":
        from .tui import run_tui

        return run_tui()
    if args.command == "search":
        try:
            tracks = search_tracks(args.query, limit=args.limit)
        except Exception as e:
            print(f"Search gagal: {e}", file=sys.stderr)
            return 1
        if not tracks:
            print(f"Tidak ada hasil untuk '{args.query}'")
            return 0
        for i, t in enumerate(tracks, start=1):
            print(format_track(i, t))
        return 0
    if args.command == "history":
        from dataclasses import asdict as _asdict

        from .history import clear_history, format_history_entry, load_history

        if args.clear:
            try:
                clear_history()
            except Exception as e:
                print(f"Gagal menghapus riwayat: {e}", file=sys.stderr)
                return 1
            print("Riwayat dihapus.")
            return 0
        try:
            entries = load_history()
        except Exception as e:
            print(f"Gagal membaca riwayat: {e}", file=sys.stderr)
            return 1
        shown = entries[: max(args.limit, 0)]
        if args.json:
            import json as _json

            print(_json.dumps([_asdict(e) for e in shown], ensure_ascii=False, indent=2))
            return 0
        if not shown:
            print("Belum ada riwayat.")
            return 0
        for i, e in enumerate(shown, start=1):
            print(format_history_entry(i, e))
        return 0
    if args.command == "play":
        print(f"Memutar {args.input} ... (Ctrl-C untuk berhenti)", file=sys.stderr)
        try:
            rc, track = play_with_metadata(args.input)
        except Exception as e:
            print(f"Gagal memutar: {e}", file=sys.stderr)
            return 1
        if rc == 0:
            try:
                from .history import add_history

                add_history(track)
            except Exception:
                pass
        return rc
    return 1
