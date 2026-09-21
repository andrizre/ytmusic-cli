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

    pp = sub.add_parser("play", help="Putar audio dari videoId, URL, atau kata kunci")
    pp.add_argument("input", help="videoId (11 char), URL music.youtube.com/youtube.com/watch/youtu.be/, atau kata kunci")
    pp.add_argument("--first", action="store_true", help="Bila query: langsung putar hasil pertama tanpa konfirmasi")
    ph = sub.add_parser("history", help="Lihat riwayat putar (cache 30 hari / 30 lagu)")
    ph.add_argument("-n", "--limit", type=int, default=30, help="Jumlah entri (default 30)")
    ph.add_argument("--clear", action="store_true", help="Hapus riwayat")
    ph.add_argument(
        "--remove",
        metavar="N",
        type=int,
        help="Hapus entri nomor N (lihat dari output 'ytmusic history'), 1 = terbaru",
    )
    ph.add_argument("--json", action="store_true", help="Output JSON")

    sub.add_parser("tui", help="Mode interaktif (ketik query, pilih, putar)")
    sub.add_parser("doctor", help="Cek mpv/ffplay, library, lokasi riwayat")
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

        from .history import (
            clear_history,
            format_history_entry,
            load_history,
            remove_history_at,
        )

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
        if args.remove is not None:
            idx = args.remove - 1  # nomor tampilan (1-based) → indeks 0-based
            try:
                _, gone = remove_history_at(idx)
            except Exception as e:
                print(f"Gagal menghapus entri: {e}", file=sys.stderr)
                return 1
            if gone is None:
                print(f"Entri nomor {args.remove} tidak ada (1-{len(entries)}).", file=sys.stderr)
                return 1
            print(f"Dihapus dari riwayat: {gone.title} — {gone.artists} [{gone.video_id}]")
            return 0
        shown = entries[: max(args.limit, 0)]
        if args.json:
            import json as _json

            print(_json.dumps([_asdict(e) for e in shown], ensure_ascii=False, indent=2))
            return 0
        if not shown:
            print("Belum ada riwayat.")
            return 0
        for i, ent in enumerate(shown, start=1):
            print(format_history_entry(i, ent))
        return 0
    if args.command == "doctor":
        from .doctor import main as _doctor

        return _doctor()
    if args.command == "play":
        from .player import is_direct_input

        target = args.input
        if not is_direct_input(target):
            try:
                tracks = search_tracks(target, limit=5 if not args.first else 1)
            except Exception as e:
                print(f"Search gagal: {e}", file=sys.stderr)
                return 1
            if not tracks:
                print(f"Tidak ada hasil untuk '{target}'")
                return 1
            if args.first:
                target = tracks[0].video_id
            else:
                for i, t in enumerate(tracks, start=1):
                    print(format_track(i, t))
                try:
                    pick = input("Pilih [1]: ").strip() or "1"
                except (EOFError, KeyboardInterrupt):
                    print(file=sys.stderr)
                    return 130
                try:
                    target = tracks[int(pick) - 1].video_id
                except (ValueError, IndexError):
                    print(f"Pilihan '{pick}' tidak valid.", file=sys.stderr)
                    return 1
        print(f"Memutar {target} ... (Ctrl-C untuk berhenti)", file=sys.stderr)
        try:
            rc, track = play_with_metadata(target)
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
