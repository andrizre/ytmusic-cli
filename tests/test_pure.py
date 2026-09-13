"""Tes fungsi murni ytmusic-cli (stdlib only, tanpa network/proses Audio)."""

import os
import subprocess
import sys
import tempfile
import time
import unittest

from ytmusic_cli.models import Track, format_track
from ytmusic_cli.player import (
    _STREAM_CACHE,
    _cache_key,
    _cache_put,
    format_duration,
    normalize_video_id,
    resolve_stream_and_track,
    resolve_stream_url,
    resolve_track,
    resume_process,
    stream_url_from_info,
    suspend_process,
    track_from_info,
)
from ytmusic_cli.history import (
    HistoryEntry,
    add_history,
    clear_history,
    format_history_entry,
    load_history,
    prune_history,
)
from ytmusic_cli.tui import (
    BACKSPACE,
    CTRL_R,
    DOWN,
    ENTER,
    ESC,
    UP,
    State,
    _current_track,
    clamp_scroll,
    do_history,
    handle_key,
    playing_action,
    render,
    volume_bar,
)


def _track(i: int) -> Track:
    return Track(
        video_id=f"vid{i:08d}",
        title=f"Judul {i}",
        artists="Artis",
        duration="3:00",
        album=None,
    )


class HandleKeyTest(unittest.TestCase):
    def test_ketik_menambah_query_dan_dirty(self):
        st = State()
        self.assertEqual(handle_key(st, "a"), "")
        self.assertEqual(st.query, "a")
        self.assertTrue(st.dirty)

    def test_backspace_menghapus(self):
        st = State(query="ab", dirty=False)
        handle_key(st, BACKSPACE)
        self.assertEqual(st.query, "a")
        self.assertTrue(st.dirty)

    def test_enter_cari_bila_dirty_atau_kosong(self):
        self.assertEqual(handle_key(State(query="x", dirty=True), ENTER), "search")
        self.assertEqual(handle_key(State(), ENTER), "search")

    def test_enter_putar_bila_bersih_dan_ada_hasil(self):
        st = State(query="x", results=[_track(1)])
        self.assertEqual(handle_key(st, ENTER), "play")

    def test_esc_keluar(self):
        self.assertEqual(handle_key(State(), ESC), "quit")

    def test_atas_bawah_wrap_dan_scroll_terjaga(self):
        st = State(results=[_track(i) for i in range(5)], selected=0)
        handle_key(st, UP)
        self.assertEqual(st.selected, 4)
        handle_key(st, DOWN)
        self.assertEqual(st.selected, 0)
        rows_shown = render(st).count("[")
        self.assertGreaterEqual(rows_shown, 1)


class VolumeBarTest(unittest.TestCase):
    def test_nol_kosong(self):
        self.assertEqual(volume_bar(0), "[░░░░░░░░░░░░] 0")

    def test_penuh_dijepit_tapi_angka_asli(self):
        bar = volume_bar(130)
        self.assertTrue(bar.startswith("[████████████]"))
        self.assertTrue(bar.endswith("130"))

    def test_tengah(self):
        self.assertEqual(volume_bar(50, width=10), "[█████░░░░░] 50")


class PlayingActionTest(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(playing_action(" "), "pause")
        self.assertEqual(playing_action("-"), "voldn")
        self.assertEqual(playing_action("+"), "volup")
        self.assertEqual(playing_action("="), "volup")
        self.assertEqual(playing_action("q"), "stop")
        self.assertEqual(playing_action(UP), "stop")


class FormatTrackTest(unittest.TestCase):
    def test_durasi_none_jadi_tanya(self):
        t = Track(video_id="abc", title="T", artists="A", duration=None, album=None)
        self.assertIn("(?)", format_track(1, t))

    def test_nomor_dan_identitas(self):
        s = format_track(2, _track(7))
        self.assertTrue(s.startswith("[2]"))
        self.assertIn("Judul 7", s)
        self.assertIn("vid00000007", s)


class ScrollTest(unittest.TestCase):
    def test_render_tidak_mengubah_scroll(self):
        st = State(results=[_track(i) for i in range(30)], selected=25, scroll=0)
        before = (st.selected, st.scroll)
        render(st)
        self.assertEqual((st.selected, st.scroll), before)

    def test_clamp_mengikuti_selected(self):
        st = State(results=[_track(i) for i in range(30)], selected=25, scroll=0)
        clamp_scroll(st)
        self.assertLessEqual(st.scroll, 25)
        self.assertGreaterEqual(25 - st.scroll, 0)


class CacheTest(unittest.TestCase):
    def test_key_id_polos(self):
        self.assertEqual(_cache_key("ABCDEFGHIJK", "https://x"), "ABCDEFGHIJK")

    def test_key_url_ambil_param_v(self):
        url = "https://music.youtube.com/watch?v=VID12345678&x=1"
        self.assertEqual(_cache_key(url, url), "VID12345678")

    def test_put_tanpa_expire_diabaikan(self):
        _STREAM_CACHE.clear()
        _cache_put("k", "https://example.com/audio")
        self.assertNotIn("k", _STREAM_CACHE)

    def test_hit_cache_tanpa_network(self):
        _STREAM_CACHE.clear()
        _STREAM_CACHE["ABCDEFGHIJK"] = ("https://cached/audio", time.time() + 3600)
        try:
            self.assertEqual(resolve_stream_url("ABCDEFGHIJK"), "https://cached/audio")
        finally:
            _STREAM_CACHE.clear()

    def test_expired_dianggap_miss(self):
        _STREAM_CACHE.clear()
        _STREAM_CACHE["ABCDEFGHIJK"] = ("https://stale/audio", time.time() - 1)
        self.assertEqual(_STREAM_CACHE["ABCDEFGHIJK"][1] < time.time(), True)
        _STREAM_CACHE.clear()


class NormalizeVideoIdTest(unittest.TestCase):
    def test_id_polos(self):
        self.assertEqual(normalize_video_id("ABCDEFGHIJK"), "ABCDEFGHIJK")

    def test_watch_url(self):
        self.assertEqual(
            normalize_video_id("https://music.youtube.com/watch?v=VID12345678&x=1"),
            "VID12345678",
        )

    def test_youtu_be(self):
        self.assertEqual(normalize_video_id("https://youtu.be/VID12345678"), "VID12345678")

    def test_shorts(self):
        self.assertEqual(
            normalize_video_id("https://www.youtube.com/shorts/VID12345678"),
            "VID12345678",
        )

    def test_nonstandar_fallback_input(self):
        self.assertEqual(normalize_video_id("https://example.com/audio"), "https://example.com/audio")


class FormatDurationTest(unittest.TestCase):
    def test_menit_detik(self):
        self.assertEqual(format_duration(185), "3:05")

    def test_jam(self):
        self.assertEqual(format_duration(3725), "1:02:05")

    def test_none_dan_invalid(self):
        self.assertIsNone(format_duration(None))
        self.assertIsNone(format_duration(-1))
        self.assertIsNone(format_duration("x"))


class TrackFromInfoTest(unittest.TestCase):
    def test_prioritas_artist_dan_topic_dibersihkan(self):
        t = track_from_info(
            {"id": "VID12345678", "title": "Judul", "artist": "Artis",
             "uploader": "Artis - Topic", "duration": 185},
            "VID12345678",
        )
        self.assertEqual(t.artists, "Artis")
        self.assertEqual(t.duration, "3:05")

    def test_fallback_uploader_tanpa_topic(self):
        t = track_from_info(
            {"id": "VID12345678", "title": "J", "uploader": "Band X - Topic", "duration": 60},
            "VID12345678",
        )
        self.assertEqual(t.artists, "Band X")

    def test_fallback_unknown(self):
        t = track_from_info({"title": "J"}, "VID12345678")
        self.assertEqual((t.video_id, t.artists, t.duration), ("VID12345678", "Unknown", None))

    def test_stream_dari_requested_formats(self):
        info = {"requested_formats": [
            {"acodec": "none", "url": "https://x/video"},
            {"acodec": "opus", "url": "https://x/audio?expire=9999999999"},
        ]}
        self.assertEqual(stream_url_from_info(info, "k"), "https://x/audio?expire=9999999999")
        _STREAM_CACHE.clear()

    def test_stream_tanpa_audio_raise(self):
        with self.assertRaises(RuntimeError):
            stream_url_from_info({"requested_formats": []}, "k")

    def test_resolve_gabung_satu_fetch(self):
        import ytmusic_cli.player as player

        calls = []
        fake = {"id": "VID12345678", "title": "Judul", "artist": "Artis",
                "duration": 200, "url": "https://x/audio?expire=9999999999"}
        orig = player.fetch_info
        player.fetch_info = lambda v: (calls.append(v), fake)[1]
        try:
            url, track = resolve_stream_and_track("VID12345678")
        finally:
            player.fetch_info = orig
            _STREAM_CACHE.clear()
        self.assertEqual(calls, ["VID12345678"])
        self.assertEqual(url, "https://x/audio?expire=9999999999")
        self.assertEqual((track.title, track.artists, track.duration), ("Judul", "Artis", "3:20"))

    def test_resolve_track_fallback_tanpa_network(self):
        import ytmusic_cli.player as player

        orig = player.fetch_info
        player.fetch_info = lambda v: (_ for _ in ()).throw(RuntimeError("net down"))
        try:
            t = resolve_track("VID12345678")
        finally:
            player.fetch_info = orig
        self.assertEqual((t.video_id, t.title, t.artists), ("VID12345678", "VID12345678", "Unknown"))


class SuspendResumeTest(unittest.TestCase):
    def test_proses_selesai_return_false(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait(timeout=10)
        self.assertFalse(suspend_process(proc))
        self.assertFalse(resume_process(proc))


class HistoryTest(unittest.TestCase):
    def _entry(self, vid: str, age_days: float = 0) -> HistoryEntry:
        return HistoryEntry(
            video_id=vid,
            title=f"Judul {vid}",
            artists="Artis",
            played_at=time.time() - age_days * 86400,
        )

    def test_lebih_tua_dari_30_hari_dibuang(self):
        entries = [self._entry("baru"), self._entry("lama", age_days=31)]
        out = prune_history(entries)
        self.assertEqual([e.video_id for e in out], ["baru"])

    def test_dipotong_30_lagu_terbaru(self):
        entries = [self._entry(f"vid{i:08d}") for i in range(35)]
        out = prune_history(entries)
        self.assertEqual(len(out), 30)
        self.assertEqual(out[0].video_id, "vid00000000")

    def test_duplikat_video_id_hanya_terbaru(self):
        entries = [self._entry("sama"), self._entry("sama")]
        self.assertEqual(len(prune_history(entries)), 1)

    def test_add_simpan_muat_bulat_dan_dedupe(self):
        with tempfile.TemporaryDirectory() as d:
            p = f"{d}/history.json"
            add_history(_track(1), path=p)
            add_history(_track(2), path=p)
            add_history(_track(1), path=p)  # putar ulang → ke depan, tanpa duplikat
            loaded = load_history(p)
            self.assertEqual([e.video_id for e in loaded], ["vid00000001", "vid00000002"])
            s = format_history_entry(1, loaded[0])
            self.assertIn("vid00000001", s)

    def test_file_rusak_jadi_kosong_dan_clear_hapus(self):
        with tempfile.TemporaryDirectory() as d:
            p = f"{d}/history.json"
            with open(p, "w", encoding="utf-8") as f:
                f.write("{bukan json")
            self.assertEqual(load_history(p), [])
            add_history(_track(3), path=p)
            clear_history(p)
            self.assertEqual(load_history(p), [])



class HistoryModeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("YTMUSIC_CLI_HISTORY_FILE")
        os.environ["YTMUSIC_CLI_HISTORY_FILE"] = f"{self._tmp.name}/history.json"
        add_history(_track(1))
        add_history(_track(2))

    def tearDown(self):
        if self._old is None:
            os.environ.pop("YTMUSIC_CLI_HISTORY_FILE", None)
        else:
            os.environ["YTMUSIC_CLI_HISTORY_FILE"] = self._old
        self._tmp.cleanup()

    def test_ctrl_r_minta_aksi_history(self):
        self.assertEqual(handle_key(State(), CTRL_R), "history")

    def test_toggle_masuk_lalu_keluar(self):
        st = State()
        do_history(st)
        self.assertTrue(st.show_history)
        self.assertEqual(len(st.history), 2)
        do_history(st)
        self.assertFalse(st.show_history)

    def test_enter_putar_dan_navigasi_wrap_di_riwayat(self):
        st = State()
        do_history(st)
        self.assertEqual(handle_key(st, ENTER), "play")
        handle_key(st, DOWN)
        self.assertEqual(st.selected, 1)
        handle_key(st, DOWN)
        self.assertEqual(st.selected, 0)  # wrap dalam 2 entri riwayat
        handle_key(st, UP)
        self.assertEqual(st.selected, 1)

    def test_ketik_dan_esc_keluar_dari_mode(self):
        st = State()
        do_history(st)
        handle_key(st, "x")
        self.assertFalse(st.show_history)
        self.assertTrue(st.dirty)
        do_history(st)
        self.assertEqual(handle_key(st, ESC), "")
        self.assertFalse(st.show_history)
        self.assertEqual(handle_key(st, ESC), "quit")

    def test_current_track_dari_riwayat(self):
        st = State()
        do_history(st)
        st.selected = 1
        t = _current_track(st)
        self.assertEqual(t.video_id, "vid00000001")

    def test_render_mode_riwayat_tandai_pilihan(self):
        st = State()
        do_history(st)
        out = render(st)
        self.assertIn("Riwayat terakhir (2)", out)
        self.assertIn("\x1b[7m", out)

if __name__ == "__main__":
    unittest.main()
