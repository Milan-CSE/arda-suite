"""listen.record_until_silence testleri — sounddevice mock'lu (gerçek mikrofon yok)."""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import numpy as np

from arda.listen import _CHUNK_SEC, _SAMPLERATE, record_follow_up, record_until_silence


def _loud_stream(chunk_size: int):
    """Her zaman eşiğin üzerinde (gürültülü) parça döndüren sahte InputStream.

    Sessizlik asla tetiklenmez → yalnızca max-süre kapağı kaydı durdurabilir."""

    class _Stream:
        def read(self, frames):
            return np.ones((chunk_size, 1), dtype="float32"), None

    @contextmanager
    def _ctx(*args, **kwargs):
        yield _Stream()

    return _ctx


class TestRecordDurationCap:
    def test_stops_at_max_sec_when_never_silent(self, tmp_path):
        """Sürekli ses gelse bile kayıt max_sec'te durmalı (sonsuz döngü koruması)."""
        chunk_size = int(_SAMPLERATE * _CHUNK_SEC)
        cfg = {"vad": {"silence_sec": 1.2, "energy_threshold": 0.01, "max_sec": 2.0}}

        with patch("arda.listen.sd.InputStream", _loud_stream(chunk_size)):
            wav = record_until_silence(cfg)

        try:
            assert wav.exists()
            # 2s sınırı: ~2 / 0.1 = 20 parça. Üst sınırı cömertçe doğrula.
            samples = wav.stat().st_size
            assert samples > 0
        finally:
            wav.unlink(missing_ok=True)

    def test_respects_silence_before_cap(self, tmp_path):
        """Sessizlik kapaktan önce gelirse, kayıt sessizlikte durmalı."""
        chunk_size = int(_SAMPLERATE * _CHUNK_SEC)
        cfg = {"vad": {"silence_sec": 0.2, "energy_threshold": 0.5, "max_sec": 60.0}}

        # Her parça sessiz (sıfır) → silence_limit hızla dolar, kapaktan çok önce durur.
        class _SilentStream:
            def read(self, frames):
                return np.zeros((chunk_size, 1), dtype="float32"), None

        @contextmanager
        def _ctx(*args, **kwargs):
            yield _SilentStream()

        with patch("arda.listen.sd.InputStream", _ctx):
            wav = record_until_silence(cfg)

        try:
            assert wav.exists()
        finally:
            wav.unlink(missing_ok=True)


class TestRecordFollowUp:
    """Sürekli sohbet takip dinlemesi: window içinde konuşma başlarsa kaydet, yoksa None."""

    def test_disabled_when_window_zero(self):
        # follow_up_window_sec<=0 → kapalı; mic bile açılmaz, hemen None.
        assert record_follow_up({"app": {"follow_up_window_sec": 0}}) is None

    def test_returns_none_when_no_speech_in_window(self):
        chunk_size = int(_SAMPLERATE * _CHUNK_SEC)
        cfg = {"vad": {"energy_threshold": 0.5, "silence_sec": 0.2, "max_sec": 60.0},
               "app": {"follow_up_window_sec": 0.5}}

        class _Silent:
            def read(self, frames):
                return np.zeros((chunk_size, 1), dtype="float32"), None

        @contextmanager
        def _ctx(*args, **kwargs):
            yield _Silent()

        with patch("arda.listen.sd.InputStream", _ctx):
            assert record_follow_up(cfg) is None       # onset yok → oturum biter

    def test_records_when_speech_starts(self):
        chunk_size = int(_SAMPLERATE * _CHUNK_SEC)
        cfg = {"vad": {"energy_threshold": 0.01, "silence_sec": 1.2, "max_sec": 0.5},
               "app": {"follow_up_window_sec": 1.0}}

        class _Loud:
            def read(self, frames):
                return np.ones((chunk_size, 1), dtype="float32"), None

        @contextmanager
        def _ctx(*args, **kwargs):
            yield _Loud()

        with patch("arda.listen.sd.InputStream", _ctx):
            wav = record_follow_up(cfg)                # onset hemen → kaydet, wav dön

        try:
            assert wav is not None and wav.exists()
        finally:
            if wav:
                wav.unlink(missing_ok=True)


_CFG = {"vad": {"energy_threshold": 0.01, "silence_sec": 0.2, "max_sec": 1.0}}


class TestBargeInWindow:
    """Barge-in penceresi akan cevapta cümleler arası KAPANMAMALI.

    Kök neden: `while speak.is_speaking()` — cümleler arası _speaking clear oluyor ve
    sonraki cümlenin `say` render'ı ~1s sürüyor. O boşlukta döngü çıkıp None dönüyordu
    → barge-in thread'i ilk cümleden sonra ölüyor, kalan cevap kesilemiyordu."""

    def test_listens_between_sentences_when_session_active(self):
        """Ses ÇALMIYOR ama akış oturumu açık → pencere açılır (is_speaking'e bakılmaz)."""
        from arda import listen, speak

        opened = []

        class _Silent:
            def read(self, frames):
                return np.zeros((frames, 1), dtype="float32"), None

        @contextmanager
        def _ctx(*args, **kwargs):
            opened.append(True)
            yield _Silent()

        active = iter([True, True, True, False])  # oturum sürüyor, sonra biter
        with patch.object(speak, "is_speaking", lambda: False), \
             patch.object(speak, "is_active", lambda: next(active, False)), \
             patch("arda.listen.sd.InputStream", _ctx):
            result = listen.record_barge_in(_CFG)

        assert opened == [True]   # cümleler arası boşlukta pencere AÇILDI
        assert result is None     # sessizlik → barge-in yok

    def test_returns_none_when_no_tts_starts(self):
        """TTS hiç başlamazsa (aktif oturum yok) kısa sürede None döner."""
        from arda import listen, speak

        with patch.object(listen, "_BARGE_IN_WAIT_SEC", 0.05), \
             patch.object(speak, "is_active", lambda: False):
            assert listen.record_barge_in(_CFG) is None

    def test_returns_none_immediately_when_should_stop_set(self):
        """should_stop True ise _BARGE_IN_WAIT_SEC beklenmeden hemen None döner."""
        from arda import listen, speak

        with patch.object(speak, "is_active", lambda: False):
            # Normalde 30s bekleyecekken should_stop=True anında döner
            assert listen.record_barge_in(_CFG, should_stop=lambda: True) is None


class TestThresholdCalibration:
    """Gürültü tabanı kalibrasyonu süreç başına BİR kez; TTS konuşurken ASLA.

    Kök neden: energy_threshold null iken _vad_params HER çağrıda 1 saniyelik sd.rec
    kalibrasyonu yapıyordu → (a) her kayıt/takip/barge-in öncesi 1s ölü zaman (ilk hece
    kesiliyor), (b) barge-in thread'i TTS konuşurken kalibre edince hoparlör sesini
    gürültü tabanı sanıp eşiği şişiriyor → barge-in hiç tetiklenmiyor."""

    def setup_method(self):
        from arda import listen
        listen._calibrated = None

    def teardown_method(self):
        from arda import listen
        listen._calibrated = None

    def test_calibrates_once_and_caches(self):
        from arda import listen

        cfg = {"vad": {"energy_threshold": None}}
        with patch.object(listen, "_calibrate_threshold", return_value=0.02) as cal:
            first = listen._vad_params(cfg)[0]
            second = listen._vad_params(cfg)[0]
        assert (first, second) == (0.02, 0.02)
        cal.assert_called_once()          # ikinci çağrı önbellekten

    def test_explicit_threshold_skips_calibration(self):
        from arda import listen

        with patch.object(listen, "_calibrate_threshold") as cal:
            threshold = listen._vad_params({"vad": {"energy_threshold": 0.033}})[0]
        assert threshold == 0.033
        cal.assert_not_called()

    def test_never_calibrates_while_tts_active(self):
        """TTS açıkken kalibrasyon yapılmaz → güvenli varsayılana düşer, önbelleğe YAZMAZ."""
        from arda import listen, speak

        with patch.object(speak, "is_active", lambda: True), \
             patch.object(listen, "_calibrate_threshold") as cal:
            threshold = listen._vad_params({"vad": {"energy_threshold": None}})[0]
        assert threshold == listen._DEFAULT_THRESHOLD
        cal.assert_not_called()
        assert listen._calibrated is None   # zehirli değer önbelleğe girmedi

    def test_calibrates_after_tts_finishes(self):
        """TTS bittikten sonraki çağrı gerçek kalibrasyonu yapar (varsayılana takılı kalmaz)."""
        from arda import listen, speak

        with patch.object(speak, "is_active", lambda: True), \
             patch.object(listen, "_calibrate_threshold", return_value=0.02):
            listen._vad_params({"vad": {"energy_threshold": None}})
        with patch.object(speak, "is_active", lambda: False), \
             patch.object(listen, "_calibrate_threshold", return_value=0.02) as cal:
            threshold = listen._vad_params({"vad": {"energy_threshold": None}})[0]
        assert threshold == 0.02
        cal.assert_called_once()
