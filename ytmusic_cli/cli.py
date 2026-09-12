import argparse
import sys

from .models import format_track
from .player import play_track
from .search import search_tracks


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ytmusic", description="YouTube Music CLI: search + play")
    sub = p.add_subparsers(dest="command")

    ps = sub.add_parser("search", help="Cari lagu")
    ps.add_argument("query", help="Kata kunci pencarian")
    ps.add_argument("-n", "--limit", type=int, default=10, help="Jumlah hasil (default 10)")

    pp = sub.add_parser("play", help="Putar audio dari videoId atau URL")
    pp.add_argument("input", help="videoId (11 char) atau URL music.youtube.com/youtube.com/watchyoutu.be/")

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
    if args.command == "play":
        print(f"Memutar {args.input} ... (Ctrl-C untuk berhenti)", file=sys.stderr)
        try:
            return play_track(args.input)
        except Exception as e:
            print(f"Gagal memutar: {e}", file=sys.stderr)
            return 1
    return 1
