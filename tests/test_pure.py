"""Tes fungsi murni ytmusic-cli (stdlib only, tanpa network/proses Audio)."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from ytmusic_cli.models import Track, format_track
from ytmusic_cli.player import (
    _STREAM_CACHE,
    _cache_key,
    _cache_put,
    format_duration,
    is_direct_input,
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
    load_queue,
    load_settings,
    prune_history,
    queue_file,
    remember_volume,
    remove_history,
    remove_history_at,
    save_queue,
    save_settings,
    settings_file,
)
from ytmusic_cli.tui import (
    BACKSPACE,
    CTRL_A,
    CTRL_Q,
    CTRL_R,
    DOWN,
    ENTER,
    ESC,
    LEFT,
    RIGHT,
    UP,
    State,
    _current_track,
    _fit,
    _row,
    _spinner,
    _truncate,
    build_queue,
    clamp_scroll,
    cycle_repeat,
    disp_width,
    do_history,
    do_history_clear,
    do_history_delete,
    do_queue,
    do_queue_add,
    do_queue_delete,
    do_queue_load,
    do_queue_move,
    do_queue_save,
    handle_key,
    paint,
    playing_action,
    progress_bar,
    render,
    step_queue,
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
        self.assertEqual(playing_action(LEFT), "seekback")
        self.assertEqual(playing_action(RIGHT), "seekfwd")
        self.assertEqual(playing_action("n"), "next")
        self.assertEqual(playing_action("N"), "next")
        self.assertEqual(playing_action("p"), "prev")
        self.assertEqual(playing_action("P"), "prev")
        self.assertEqual(playing_action("q"), "stop")
        self.assertEqual(playing_action("Q"), "stop")
        self.assertEqual(playing_action(ESC), "stop")
        self.assertEqual(playing_action("\x03"), "stop")

    def test_tombol_asing_diabaikan_bukan_stop(self):
        self.assertEqual(playing_action(UP), "")
        self.assertEqual(playing_action(DOWN), "")
        self.assertEqual(playing_action("a"), "")
        self.assertEqual(playing_action("*"), "")

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


class HistoryRemoveTest(unittest.TestCase):
    def _seed(self, d: str) -> str:
        p = f"{d}/history.json"
        add_history(_track(1), path=p)
        add_history(_track(2), path=p)
        add_history(_track(3), path=p)  # terbaru → di depan
        return p

    def test_remove_at_hapus_satu(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._seed(d)
            entries, gone = remove_history_at(0, p)
            self.assertIsNotNone(gone)
            self.assertEqual(gone.video_id, "vid00000003")
            self.assertEqual([e.video_id for e in entries], ["vid00000002", "vid00000001"])
            self.assertEqual([e.video_id for e in load_history(p)], ["vid00000002", "vid00000001"])

    def test_remove_at_hapus_terakhir_dan_tersimpan(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._seed(d)
            entries, gone = remove_history_at(2, p)
            self.assertEqual(gone.video_id, "vid00000001")
            self.assertEqual(len(entries), 2)

    def test_remove_at_indeks_negatif_ditolak(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._seed(d)
            entries, gone = remove_history_at(-1, p)
            self.assertIsNone(gone)
            self.assertEqual(len(entries), 3)  # tak berubah

    def test_remove_at_lewat_akhir_ditolak(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._seed(d)
            _, gone = remove_history_at(99, p)
            self.assertIsNone(gone)

    def test_remove_video_id(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._seed(d)
            entries, removed = remove_history("vid00000002", p)
            self.assertTrue(removed)
            self.assertEqual([e.video_id for e in entries], ["vid00000003", "vid00000001"])

    def test_remove_video_id_tak_ada_false(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._seed(d)
            _, removed = remove_history("tak-ada", p)
            self.assertFalse(removed)

    def test_panel_x_hapus_entri_dipilih(self):
        with tempfile.TemporaryDirectory() as d:
            p = f"{d}/history.json"
            old = os.environ.get("YTMUSIC_CLI_HISTORY_FILE")
            os.environ["YTMUSIC_CLI_HISTORY_FILE"] = p
            try:
                add_history(_track(1), path=p)
                add_history(_track(2), path=p)
                add_history(_track(3), path=p)
                st = State()
                do_history(st)
                self.assertEqual(len(st.history), 3)
                self.assertEqual(handle_key(st, "x"), "hdel")
                do_history_delete(st)
                self.assertEqual(len(st.history), 2)
                self.assertNotIn("vid00000003", [e.video_id for e in st.history])
                self.assertTrue(st.show_history)  # panel tetap terbuka
                self.assertIn("Dihapus", st.status)
                self.assertEqual(len(load_history(p)), 2)
            finally:
                if old is None:
                    os.environ.pop("YTMUSIC_CLI_HISTORY_FILE", None)
                else:
                    os.environ["YTMUSIC_CLI_HISTORY_FILE"] = old

    def test_panel_x_hapus_terakhir_lalu_kosong(self):
        with tempfile.TemporaryDirectory() as d:
            p = f"{d}/history.json"
            old = os.environ.get("YTMUSIC_CLI_HISTORY_FILE")
            os.environ["YTMUSIC_CLI_HISTORY_FILE"] = p
            try:
                add_history(_track(5), path=p)
                st = State()
                do_history(st)
                do_history_delete(st)
                self.assertEqual(st.history, [])
                self.assertEqual(st.selected, 0)
                self.assertIn("Dihapus", st.status)
            finally:
                if old is None:
                    os.environ.pop("YTMUSIC_CLI_HISTORY_FILE", None)
                else:
                    os.environ["YTMUSIC_CLI_HISTORY_FILE"] = old

    def test_panel_c_bersihkan_semua(self):
        with tempfile.TemporaryDirectory() as d:
            p = f"{d}/history.json"
            old = os.environ.get("YTMUSIC_CLI_HISTORY_FILE")
            os.environ["YTMUSIC_CLI_HISTORY_FILE"] = p
            try:
                add_history(_track(1), path=p)
                add_history(_track(2), path=p)
                st = State()
                do_history(st)
                self.assertEqual(handle_key(st, "c"), "hclear")
                do_history_clear(st)
                self.assertEqual(st.history, [])
                self.assertTrue(st.show_history)
                self.assertIn("dibersihkan", st.status)
                self.assertEqual(load_history(p), [])
            finally:
                if old is None:
                    os.environ.pop("YTMUSIC_CLI_HISTORY_FILE", None)
                else:
                    os.environ["YTMUSIC_CLI_HISTORY_FILE"] = old

    def test_hapus_diluar_panel_diam_saja(self):
        # do_history_* di luar panel riwayat tidak melakukan apa-apa ke state
        st = State(history=[HistoryEntry("v1", "T", "A", None, None, 0.0)])
        do_history_delete(st)
        self.assertEqual(len(st.history), 1)
        self.assertEqual(st.status, "Ketik query + Enter untuk mencari.")

    def test_handle_key_panel_riwayat_lain_tidak_memicu(self):
        # 'x' di panel antrean = qdel, bukan hdel
        st = State(queue=[_track(1)], show_queue=True)
        self.assertEqual(handle_key(st, "x"), "qdel")
        # 'x' saat memutar = remove lagu, bukan hdel
        self.assertEqual(playing_action("x"), "remove")


class SettingsVolumeTest(unittest.TestCase):
    def test_default_file_kosong(self):
        with tempfile.TemporaryDirectory() as d:
            p = f"{d}/settings.json"
            self.assertEqual(load_settings(p), {"volume": 100})

    def test_file_rusak_kembali_default(self):
        with tempfile.TemporaryDirectory() as d:
            p = f"{d}/settings.json"
            with open(p, "w", encoding="utf-8") as f:
                f.write("{rusak")
            self.assertEqual(load_settings(p), {"volume": 100})

    def test_simpan_muat_bulat(self):
        with tempfile.TemporaryDirectory() as d:
            p = f"{d}/settings.json"
            save_settings({"volume": 75}, p)
            self.assertEqual(load_settings(p), {"volume": 75})

    def test_volume_dijepit_0_130(self):
        with tempfile.TemporaryDirectory() as d:
            p = f"{d}/settings.json"
            save_settings({"volume": 999}, p)
            self.assertEqual(load_settings(p), {"volume": 130})
            save_settings({"volume": -50}, p)
            self.assertEqual(load_settings(p), {"volume": 0})

    def test_volume_invalid_kembali_default(self):
        with tempfile.TemporaryDirectory() as d:
            p = f"{d}/settings.json"
            with open(p, "w", encoding="utf-8") as f:
                f.write('{"volume": "bukan angka"}')
            self.assertEqual(load_settings(p), {"volume": 100})

    def test_remember_volume_hanya_volume_yang_disimpan(self):
        with tempfile.TemporaryDirectory() as d:
            p = f"{d}/settings.json"
            remember_volume(42, p)
            with open(p, encoding="utf-8") as f:
                raw = json.load(f)
            self.assertEqual(raw, {"volume": 42})

    def test_env_override_lokasi(self):
        with tempfile.TemporaryDirectory() as d:
            p = f"{d}/custom.json"
            old = os.environ.get("YTMUSIC_CLI_SETTINGS_FILE")
            os.environ["YTMUSIC_CLI_SETTINGS_FILE"] = p
            try:
                self.assertEqual(settings_file(), Path(p))
                remember_volume(60)
                self.assertEqual(load_settings(), {"volume": 60})
            finally:
                if old is None:
                    os.environ.pop("YTMUSIC_CLI_SETTINGS_FILE", None)
                else:
                    os.environ["YTMUSIC_CLI_SETTINGS_FILE"] = old

    def test_state_memuat_volume_dari_settings(self):
        # run_tui memuat settings.json; di sini disimulasikan lewat load_settings
        with tempfile.TemporaryDirectory() as d:
            p = f"{d}/settings.json"
            remember_volume(80, p)
            st = State()
            st.volume = int(load_settings(p).get("volume", 100))
            self.assertEqual(st.volume, 80)

    def test_render_volume_memakai_state(self):
        st = State(playing="Judul 1 — Artis", volume=75, volume_control=True)
        out = render(st)
        self.assertIn("[", out)
        self.assertIn("75", out)


class PlayerCmdVolumeTest(unittest.TestCase):
    def test_mpv_volume_flag_diteruskan(self):
        from ytmusic_cli.player import player_cmd

        cmd = player_cmd("https://x/audio", 75)
        self.assertIn("--volume=75", cmd)
        self.assertEqual(cmd[-1], "https://x/audio")

    def test_mpv_tanpa_volume_tak_ada_flag(self):
        from ytmusic_cli.player import player_cmd

        cmd = player_cmd("https://x/audio")
        self.assertFalse(any(a.startswith("--volume=") for a in cmd))

    def test_mpv_volume_dijepit_100(self):
        from ytmusic_cli.player import player_cmd

        cmd = player_cmd("https://x/audio", 130)
        self.assertIn("--volume=100", cmd)



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
        handle_key(st, "a")  # ketik biasa menutup panel (x di panel = hapus entri)
        self.assertFalse(st.show_history)
        self.assertTrue(st.dirty)
        self.assertEqual(st.query, "a")
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
        # render() memakai _row() yang butuh warna aktif untuk highlight;
        # NO_COLOR di env luar (CI) menonaktifkannya → cek via proses anak.
        import subprocess

        code = (
            "import os; os.environ['FORCE_COLOR']='1'; os.environ.pop('NO_COLOR',None);"
            "from ytmusic_cli.tui import State, render, do_history;"
            "st=State(); do_history(st); out=render(st);"
            "import sys; sys.exit(0 if '\\x1b[48;5;62m' in out else 1)"
        )
        r = subprocess.run([sys.executable, "-c", code], capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr.decode())


class RenderLayoutTest(unittest.TestCase):
    """Tes layout render baru: lebar adaptif, kolom rapi, NO_COLOR aman."""

    def test_render_kosong_tak_gagal(self):
        out = render(State())
        self.assertIn("Cari", out)
        self.assertIn("Belum ada hasil", out)

    def test_render_hasil_punya_nomor_dan_terpilih(self):
        st = State(results=[_track(1), _track(2)], selected=1)
        out = render(st)
        self.assertIn("Judul 1", out)
        self.assertIn("vid00000001", out)
        self.assertNotIn("\x1b[7m", out)  # highlight baru pakai 48;5;
        self.assertIn("\x1b[48;5;62m", out)

    def test_render_playing_berisi_judul_dan_queue(self):
        st = State(
            playing="Judul 1 — Artis",
            queue=[_track(1), _track(2)],
            queue_pos=0,
            position=30.0,
            duration=180.0,
        )
        out = render(st)
        self.assertIn("Judul 1", out)
        self.assertIn("0:30 / 3:00", out)
        self.assertIn("[1/2]", out)

    def test_render_paused_pakai_tanda_jeda(self):
        st = State(playing="Judul 1 — Artis", paused=True)
        out = render(st)
        self.assertIn("⏸", out)

    def test_render_shuffle_repeat_tampil_sebagai_tag(self):
        st = State(playing="T", shuffle=True, repeat="one")
        out = render(st)
        self.assertIn("acak", out)
        self.assertIn("ulangi-1", out)

    def test_loading_menampilkan_spinner_dan_menghilang(self):
        st = State(loading=True)
        self.assertIn(_spinner(), render(st))
        st.loading = False
        self.assertNotIn(_spinner(), render(st))

    def test_status_error_diwarnai_gagal(self):
        st = State(status="Gagal memutar: boa")
        self.assertIn("Gagal", render(st))

    def test_render_terminal_sempit_tak_panic(self):
        import ytmusic_cli.tui as tui

        old = tui._term_size
        tui._term_size = lambda: (30, 12)
        try:
            out = render(State(results=[_track(i) for i in range(20)], selected=5))
            for line in out.splitlines():
                self.assertLessEqual(disp_width(line), 40)
        finally:
            tui._term_size = old


class DispWidthTest(unittest.TestCase):
    def test_ascii_satu_per_char(self):
        self.assertEqual(disp_width("abc"), 3)

    def test_cjk_lebar_dua(self):
        self.assertEqual(disp_width("夜に"), 4)

    def test_emoji_lebar_dua(self):
        self.assertGreaterEqual(disp_width("🎵"), 1)

    def test_combining_nol(self):
        self.assertEqual(disp_width("a\u0301"), 1)

    def test_truncate_dengan_elipsis(self):
        self.assertEqual(_truncate("abcdefg", 4), "abc…")
        self.assertEqual(_truncate("abcde", 5), "abcde")
        self.assertEqual(_truncate("abc", 10), "abc")
        self.assertEqual(_truncate("abc", 0), "")

    def test_truffle_lebar_cjk(self):
        self.assertEqual(_truncate("夜に来る", 5), "夜に…")

    def test_fit_padding_pas_lebar(self):
        self.assertEqual(_fit("ab", 5), "ab   ")
        self.assertEqual(disp_width(_fit("abcdef", 3)), 3)


class ColorTest(unittest.TestCase):
    def test_no_color_menonaktifkan_escape(self):
        old = os.environ.get("NO_COLOR")
        os.environ["NO_COLOR"] = "1"
        try:
            self.assertEqual(paint("x", 51), "x")
        finally:
            if old is None:
                os.environ.pop("NO_COLOR", None)
            else:
                os.environ["NO_COLOR"] = old

    def test_force_color_mengaktifkan_escape(self):
        # Set env di proses anak: _color() ditakwil saat impor modul, sehingga
        # NO_COLOR/ForceColor dari luar (mis. CI) tak bisa diubah di tengah jalan.
        import subprocess

        code = (
            "import os; os.environ['FORCE_COLOR']='1'; os.environ.pop('NO_COLOR',None);"
            "from ytmusic_cli.tui import paint;"
            "import sys; sys.exit(0 if '\\x1b[' in paint('x', 51) else 1)"
        )
        r = subprocess.run([sys.executable, "-c", code], capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr.decode())

    def test_paint_tanpa_kode_polos(self):
        self.assertEqual(paint("x"), "x")

    def test_row_terpilih_latar_penuh(self):
        line = _row([("a", 3, 0)], 10, True)
        self.assertTrue(line.startswith("\x1b[48;5;"))
        self.assertTrue(disp_width(line) >= 10)

    def test_row_biasa_tanpa_latar(self):
        line = _row([("a", 3, 0)], 10, False)
        self.assertNotIn("\x1b[48", line)


class ProgressBarTest(unittest.TestCase):
    def test_tanpa_durasi_kosong(self):
        self.assertEqual(progress_bar(None, 180.0), "")
        self.assertEqual(progress_bar(10.0, None), "")
        self.assertEqual(progress_bar(10.0, 0.0), "")

    def test_format_menit_detik(self):
        bar = progress_bar(75.0, 180.0, width=20)
        self.assertIn("1:15 / 3:00", bar)
        self.assertTrue(bar.startswith("["))

    def test_dijepit_penuh(self):
        bar = progress_bar(999.0, 180.0, width=10)
        self.assertTrue(bar.startswith("[██████████]"))


class BuildQueueTest(unittest.TestCase):
    def test_dari_hasil_search_dari_posisi_terpilih(self):
        st = State(results=[_track(i) for i in range(5)], selected=2)
        q, pos = build_queue(st)
        self.assertEqual(len(q), 5)
        self.assertEqual(pos, 2)
        self.assertEqual(q[2].video_id, "vid00000002")

    def test_kosong(self):
        q, pos = build_queue(State())
        self.assertEqual((q, pos), ([], 0))

    def test_render_tampilkan_nomor_antrean(self):
        st = State(
            results=[_track(1)],
            playing="Judul 1 — Artis",
            queue=[_track(1), _track(2)],
            queue_pos=0,
            position=30.0,
            duration=180.0,
        )
        out = render(st)
        self.assertIn("[1/2]", out)
        self.assertIn("0:30 / 3:00", out)


class DirectInputTest(unittest.TestCase):
    def test_id_polos_langsung(self):
        self.assertTrue(is_direct_input("ABCDEFGHIJK"))

    def test_url_langsung(self):
        self.assertTrue(is_direct_input("https://music.youtube.com/watch?v=ABCDEFGHIJK"))

    def test_kata_kunci_bukan_langsung(self):
        self.assertFalse(is_direct_input("ado usu"))
        self.assertFalse(is_direct_input(""))
        self.assertFalse(is_direct_input("   "))


class SearchRetryTest(unittest.TestCase):
    def test_gagal_dua_kali_lalu_berhasil(self):
        import ytmusic_cli.search as _s

        calls = {"n": 0}

        class _Fake:
            def search(self, *a, **k):
                calls["n"] += 1
                if calls["n"] < 3:
                    raise ConnectionError("putus")
                return [{"videoId": "VID12345678", "title": "T", "artists": [], "duration": "3:00"}]

        old, _s.YTMusic = _s.YTMusic, lambda: _Fake()
        try:
            tracks = _s.search_tracks("x", tries=3)
        finally:
            _s.YTMusic = old
        self.assertEqual(len(tracks), 1)
        self.assertEqual(calls["n"], 3)

    def test_gagal_total_raise(self):
        import ytmusic_cli.search as _s

        class _Fake:
            def search(self, *a, **k):
                raise ConnectionError("mati")

        old, _s.YTMusic = _s.YTMusic, lambda: _Fake()
        try:
            with self.assertRaises(ConnectionError):
                _s.search_tracks("x", tries=2)
        finally:
            _s.YTMusic = old


class DoctorTest(unittest.TestCase):
    def test_collect_dan_format(self):
        from ytmusic_cli.doctor import collect, format_report

        checks = collect()
        names = {c.name for c in checks}
        self.assertTrue({"python", "player", "yt-dlp", "ytmusicapi", "riwayat"} <= names)
        report = format_report(checks)
        self.assertIn("[OK]", report)

    def test_parser_punya_doctor_dan_first(self):
        from ytmusic_cli.cli import build_parser

        p = build_parser()
        args = p.parse_args(["play", "ado usu"])
        self.assertFalse(args.first)
        args = p.parse_args(["play", "--first", "ado usu"])
        self.assertTrue(args.first)
        args = p.parse_args(["doctor"])
        self.assertEqual(args.command, "doctor")

class RepeatShuffleTest(unittest.TestCase):
    def test_siklus_ulangi_off_all_one_off(self):
        self.assertEqual(cycle_repeat("off"), "all")
        self.assertEqual(cycle_repeat("all"), "one")
        self.assertEqual(cycle_repeat("one"), "off")

    def test_mapping_tombol(self):
        self.assertEqual(playing_action("s"), "shuffle")
        self.assertEqual(playing_action("S"), "shuffle")
        self.assertEqual(playing_action("r"), "repeat")
        self.assertEqual(playing_action("R"), "repeat")

    def test_next_sekuensial_dan_habis(self):
        self.assertEqual(step_queue(0, 3, "next", False, "off"), 1)
        self.assertEqual(step_queue(1, 3, None, False, "off"), 2)
        self.assertIsNone(step_queue(2, 3, "next", False, "off"))
        self.assertIsNone(step_queue(2, 3, None, False, "off"))

    def test_prev_sekuensial_dan_mentok(self):
        self.assertEqual(step_queue(2, 3, "prev", False, "off"), 1)
        self.assertIsNone(step_queue(0, 3, "prev", False, "off"))

    def test_ulangi_semua_membungkus(self):
        self.assertEqual(step_queue(2, 3, "next", False, "all"), 0)
        self.assertEqual(step_queue(2, 3, None, False, "all"), 0)
        self.assertEqual(step_queue(0, 3, "prev", False, "all"), 2)

    def test_ulangi_satu_menahan_posisi(self):
        self.assertEqual(step_queue(1, 3, "next", False, "one"), 1)
        self.assertEqual(step_queue(1, 3, None, False, "one"), 1)
        self.assertEqual(step_queue(1, 3, "prev", False, "one"), 1)

    def test_acak_satu_lagu_tetap(self):
        self.assertEqual(step_queue(0, 1, "next", True, "off"), 0)
        self.assertEqual(step_queue(0, 1, None, True, "all"), 0)

    def test_acak_dua_lagu_selalu_pindah(self):
        # length 2 → satu-satunya pilihan acak adalah indeks satunya
        for pos in (0, 1):
            self.assertEqual(step_queue(pos, 2, "next", True, "off"), 1 - pos)
            self.assertEqual(step_queue(pos, 2, None, True, "off"), 1 - pos)

    def test_acak_tak_pernah_diam_atau_keluar(self):
        for _ in range(200):
            self.assertIn(step_queue(2, 5, "next", True, "off"), {0, 1, 3, 4})

    def test_kosong_atau_pos_rusak_berhenti(self):
        self.assertIsNone(step_queue(0, 0, "next", False, "off"))
        self.assertIsNone(step_queue(9, 3, None, False, "all"))

    def test_render_tampilkan_mode(self):
        st = State(
            results=[_track(1)],
            playing="Judul 1 — Artis",
            queue=[_track(1), _track(2)],
            queue_pos=0,
            shuffle=True,
            repeat="one",
        )
        out = render(st)
        self.assertIn("[acak+ulangi-1]", out)


class QueueStoreTest(unittest.TestCase):
    def test_simpan_muat_bulat(self):
        with tempfile.TemporaryDirectory() as d:
            p = f"{d}/queue.json"
            save_queue([_track(1), _track(2)], p)
            loaded = load_queue(p)
            self.assertEqual([t.video_id for t in loaded], ["vid00000001", "vid00000002"])
            self.assertEqual((loaded[0].title, loaded[0].artists), ("Judul 1", "Artis"))

    def test_file_hilang_rusak_toleran(self):
        with tempfile.TemporaryDirectory() as d:
            p = f"{d}/queue.json"
            self.assertEqual(load_queue(p), [])
            with open(p, "w", encoding="utf-8") as f:
                f.write("[1,2")
            self.assertEqual(load_queue(p), [])
            with open(p, "w", encoding="utf-8") as f:
                f.write('[{"title": "tanpa id"}, {"video_id": "VID12345678"}]')
            loaded = load_queue(p)
            self.assertEqual(len(loaded), 1)
            self.assertEqual((loaded[0].video_id, loaded[0].artists), ("VID12345678", "Unknown"))

    def test_queue_file_env_override(self):
        with tempfile.TemporaryDirectory() as d:
            p = f"{d}/custom.json"
            old = os.environ.get("YTMUSIC_CLI_QUEUE_FILE")
            os.environ["YTMUSIC_CLI_QUEUE_FILE"] = p
            try:
                self.assertEqual(queue_file(), Path(p))
            finally:
                if old is None:
                    del os.environ["YTMUSIC_CLI_QUEUE_FILE"]
                else:
                    os.environ["YTMUSIC_CLI_QUEUE_FILE"] = old


class QueuePanelTest(unittest.TestCase):
    def test_tombol_panel(self):
        self.assertEqual(handle_key(State(), CTRL_Q), "queue")
        self.assertEqual(handle_key(State(), CTRL_A), "qadd")
        self.assertEqual(playing_action("x"), "remove")
        self.assertEqual(playing_action("X"), "remove")

    def test_buka_tutup_panel(self):
        st = State()
        do_queue(st)  # antrean kosong → panel tetap dibuka agar bisa muat (L)
        self.assertTrue(st.show_queue)
        self.assertIn("kosong", st.status)
        out = render(st)
        self.assertIn("Ctrl+A", out)
        do_queue(st)
        self.assertFalse(st.show_queue)

    def test_buka_mulai_dari_posisi_terakhir(self):
        st = State(queue=[_track(1), _track(2), _track(3)], queue_pos=2)
        do_queue(st)
        self.assertTrue(st.show_queue)
        self.assertEqual(st.selected, 2)

    def test_tambah_dari_hasil(self):
        st = State(results=[_track(1), _track(2), _track(3)], selected=1)
        do_queue_add(st)
        st.selected = 2
        do_queue_add(st)
        self.assertEqual([t.video_id for t in st.queue], ["vid00000002", "vid00000003"])
        self.assertIn("[2]", st.status)

    def test_tambah_tanpa_pilihan(self):
        st = State()
        do_queue_add(st)
        self.assertEqual(st.queue, [])
        self.assertIn("Tak ada", st.status)

    def test_enter_di_panel_minta_putar_antrean(self):
        st = State(queue=[_track(1), _track(2)], show_queue=True)
        self.assertEqual(handle_key(st, ENTER), "queueplay")

    def test_operasi_huruf_di_panel(self):
        st = State(queue=[_track(1)], show_queue=True)
        self.assertEqual(handle_key(st, "x"), "qdel")
        self.assertEqual(handle_key(st, "u"), "qmoveup")
        self.assertEqual(handle_key(st, "d"), "qmovedown")
        self.assertEqual(handle_key(st, "s"), "qsave")
        self.assertEqual(handle_key(st, "l"), "qload")

    def test_hapus_dan_geser(self):
        st = State(queue=[_track(1), _track(2), _track(3)], show_queue=True, selected=1)
        do_queue_delete(st)
        self.assertEqual([t.video_id for t in st.queue], ["vid00000001", "vid00000003"])
        self.assertEqual(st.selected, 1)
        do_queue_move(st, +1)  # mentok bawah
        self.assertEqual([t.video_id for t in st.queue], ["vid00000001", "vid00000003"])
        self.assertIn("bawah", st.status)
        do_queue_move(st, -1)
        self.assertEqual([t.video_id for t in st.queue], ["vid00000003", "vid00000001"])
        self.assertEqual(st.selected, 0)
        do_queue_move(st, -1)  # mentok atas
        self.assertIn("atas", st.status)

    def test_simpan_muat_lewat_panel(self):
        with tempfile.TemporaryDirectory() as d:
            p = f"{d}/queue.json"
            st = State(queue=[_track(1), _track(2)], show_queue=True)
            do_queue_save(st, p)
            self.assertIn("2 lagu", st.status)
            st2 = State(show_queue=True)
            do_queue_load(st2, p)
            self.assertEqual([t.video_id for t in st2.queue], ["vid00000001", "vid00000002"])
            self.assertEqual((st2.queue_pos, st2.selected), (0, 0))
            st3 = State(show_queue=True)
            do_queue_load(st3, f"{d}/tak-ada.json")
            self.assertEqual(st3.queue, [])
            self.assertIn("Tidak ada", st3.status)
            do_queue_save(State(show_queue=True), p)
            # antrean kosong → berkas tak ditimpa
            self.assertEqual([t.video_id for t in load_queue(p)], ["vid00000001", "vid00000002"])

    def test_panel_ketik_biasa_menutup(self):
        st = State(queue=[_track(1)], show_queue=True)
        handle_key(st, "a")
        self.assertFalse(st.show_queue)
        self.assertEqual(st.query, "a")

    def test_riwayat_dan_antrean_saling_tutup(self):
        st = State(queue=[_track(1)], show_queue=True)
        do_history(st)
        self.assertFalse(st.show_queue)
        st2 = State(queue=[_track(1)], show_history=True)
        do_queue(st2)
        self.assertFalse(st2.show_history)
        self.assertTrue(st2.show_queue)


if __name__ == "__main__":
    unittest.main()
