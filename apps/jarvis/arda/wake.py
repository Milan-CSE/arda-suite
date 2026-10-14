"""Wake word tespiti — dört backend (`config.wake.backend`):

  "oww"      → openWakeWord modeliyle sürekli düşük maliyetli dinleme.
  "whisper"  → Whisper STT ile chunk-bazlı wake phrase tespiti; aksan-bağımsız.
  "vosk"     → Vosk KaldiRecognizer ile offline keyword tespiti (düşük CPU).
  "parakeet" → NVIDIA Parakeet STT (whisper backend'iyle aynı sliding-window mantığı).

Tüm listener'lar aynı `wait_for_wake()` arayüzünü uygular; `make_listener(cfg)`
factory config'e göre doğru olanı döndürür. Konuşmacı doğrulaması (speaker_verify)
yalnızca STT-tabanlı backend'lerde (whisper/parakeet) uygulanır.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

import numpy as np

from arda.constants import STT_MODEL, WAKE_PHRASE

log = logging.getLogger(__name__)
import sounddevice as sd

_SAMPLERATE = 16000
_FRAME = 1280  # 80 ms @ 16 kHz

# Repo kökü → göreli model/profil yolları cwd'den BAĞIMSIZ çözülür: `.app` paketi farklı
# çalışma dizininden başlasa bile model/profil bulunur (F2). HF repo-id'leri (whisper/
# parakeet model adları) yerel yol DEĞİLDİR; bu fonksiyondan geçmez.
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _resolve_local_path(path: str) -> str:
    """Göreli yerel dosya/dizin yolunu repo köküne göre mutlak yap; mutlak yol aynen döner."""
    p = Path(path).expanduser()
    return str(p if p.is_absolute() else _REPO_ROOT / p)


def _load_oww_model(wake: dict):
    """openWakeWord modelini yükle (lazy import → modül testte dep gerektirmez)."""
    try:
        from openwakeword.model import Model
    except ImportError as exc:
        raise RuntimeError(
            "openwakeword kurulu değil (wake.backend: oww). Kur: .venv/bin/pip install openwakeword"
        ) from exc
    model_path = wake.get("model_path")
    framework = wake.get("framework", "onnx")
    if model_path:
        return Model(wakeword_models=[model_path], inference_framework=framework)
    return Model(inference_framework=framework)


@contextmanager
def _default_stream():
    """Gerçek mikrofon akışı (16 kHz, int16, 80 ms kare)."""
    with sd.InputStream(
        samplerate=_SAMPLERATE, channels=1, dtype="int16", blocksize=_FRAME
    ) as stream:
        yield stream


class WakeListener:
    """openWakeWord tabanlı wake listener. Model skoru eşiği geçince tetikler."""

    def __init__(self, cfg: dict, *, model=None) -> None:
        wake = cfg.get("wake", {})
        self._threshold = float(wake.get("threshold", 0.5))
        self._model = model if model is not None else _load_oww_model(wake)

    @staticmethod
    def _triggered(scores: dict, threshold: float) -> bool:
        return any(score >= threshold for score in scores.values())

    def wait_for_wake(
        self,
        *,
        stream_factory: Callable = _default_stream,
        stop: Callable[[], bool] = lambda: False,
        triggered: Callable[[], bool] = lambda: False,
        wake_on: Callable[[], bool] = lambda: True,
    ) -> bool:
        with stream_factory() as stream:
            while not stop():
                if triggered():
                    return True
                frame, _ = stream.read(_FRAME)
                if wake_on():
                    scores = self._model.predict(np.asarray(frame).flatten())
                    if self._triggered(scores, self._threshold):
                        return True
        return False


class WhisperWakeListener:
    """Whisper STT tabanlı wake listener.

    Her `chunk_sec` saniyede bir sesi float32 numpy array olarak Whisper'a verir;
    transcription `phrase` içeriyorsa tetikler. openWakeWord'ün aksine aksan ve
    dil bağımsızdır — wake word Türkçe veya İngilizce söylenebilir.
    """

    def __init__(self, cfg: dict, *, transcribe_fn: Callable | None = None, verify_fn: Callable | None = None) -> None:
        wake = cfg.get("wake", {})
        stt = cfg.get("stt", {})
        self._phrase = wake.get("phrase", WAKE_PHRASE).lower()
        self._chunk_sec = float(wake.get("chunk_sec", 1.5))
        # wake word için özel hafif model; yoksa stt.model'e düşer
        self._model_path = wake.get("whisper_model") or stt.get("model", STT_MODEL)
        # Wake için stt diliyle aynı dil kullan (Türkçe "jarvis" Whisper'da daha güvenilir)
        self._language = wake.get("language") or stt.get("language", "tr")
        # konuşmacı doğrulama
        self._speaker_verify = bool(wake.get("speaker_verify", False))
        self._speaker_threshold = float(wake.get("speaker_threshold", 0.75))
        self._speaker_profile_path = _resolve_local_path(wake.get("speaker_profile", "models/speaker_profile.npy"))
        self._speaker_profile = self._load_profile()
        # test için inject edilebilir; üretimde None → gerçek impl kullanılır
        self._transcribe_fn = transcribe_fn
        self._verify_fn = verify_fn  # test için inject (None → speaker.verify)

    def _load_profile(self):
        if not self._speaker_verify:
            return None
        from pathlib import Path
        p = Path(self._speaker_profile_path)
        if not p.exists():
            import logging
            logging.getLogger(__name__).warning(
                "speaker_verify açık ama profil bulunamadı: %s — doğrulama devre dışı. "
                "Kayıt için: .venv/bin/python scripts/enroll_speaker.py", p
            )
            return None
        from arda.speaker import load_profile
        return load_profile(p)

    def _is_speaker(self, audio: np.ndarray) -> bool:
        if not self._speaker_verify:
            return True
        if self._verify_fn is not None:
            return self._verify_fn(audio)
        if self._speaker_profile is None:
            return True  # profil yok → doğrulama devre dışı (uyarı zaten loglandı)
        from arda.speaker import verify
        return verify(audio, self._speaker_profile, self._speaker_threshold)

    def _transcribe(self, audio: np.ndarray) -> str:
        if self._transcribe_fn is not None:
            return self._transcribe_fn(audio)
        from arda.transcribe import transcribe_mlx

        result = transcribe_mlx(
            audio,
            model=self._model_path,
            language=self._language,
        )
        return result.get("text", "").strip()

    _HALLUCINATION_RE = __import__("re").compile(r"(.)\1{10,}")  # aynı karakter 10+ kez

    def _matches(self, text: str) -> bool:
        t = text.lower().strip()
        if not t or self._HALLUCINATION_RE.search(t):
            return False
        return self._phrase in t

    def wait_for_wake(
        self,
        *,
        stream_factory: Callable = _default_stream,
        stop: Callable[[], bool] = lambda: False,
        triggered: Callable[[], bool] = lambda: False,
        wake_on: Callable[[], bool] = lambda: True,
    ) -> bool:
        # Sliding window: her step_sec'te son window_sec'i Whisper'a ver.
        # Ses chunk sınırına bölünmez → "hey" / "jarvis" her zaman aynı pencerede.
        window_samples = int(self._chunk_sec * _SAMPLERATE)  # örn. 2 sn = 32000
        step_samples = max(_FRAME, window_samples // 4)       # örn. 0.5 sn = 8000

        buf = np.zeros(0, dtype=np.int16)
        step_acc = 0  # bir sonraki transcribe'a kadar biriken sample sayısı

        with stream_factory() as stream:
            while not stop():
                if triggered():
                    return True
                frame, _ = stream.read(_FRAME)
                if not wake_on():
                    continue

                chunk = np.asarray(frame).flatten()
                buf = np.concatenate([buf, chunk])
                if len(buf) > window_samples:
                    buf = buf[-window_samples:]
                step_acc += len(chunk)

                if step_acc < step_samples:
                    continue
                step_acc = 0

                # En az bir adımlık ses biriksin; ama window küçükse (window<=2*step)
                # window'a cap'lendiği için step_samples*2'ye asla ulaşmaz → deadlock.
                # Bu yüzden minimum eşiği window_samples ile sınırla.
                if len(buf) < min(step_samples * 2, window_samples):
                    continue  # henüz çok az ses var

                audio_f32 = buf.astype(np.float32) / 32768.0
                text = self._transcribe(audio_f32)
                log.debug("wake transcribe: %r", text)

                if self._matches(text):
                    if self._is_speaker(audio_f32):
                        log.info("wake tetiklendi: %r", text)
                        return True
                    else:
                        log.info("wake phrase bulundu ama ses tanınmadı: %r", text)

        return False


class VoskWakeListener:
    """Vosk STT ile wake keyword tespiti — offline, API key yok, Türkçe aksanla çalışır.

    Vosk KaldiRecognizer'ı yalnızca keyword listesiyle başlatır → her frame'de sadece
    o kelimeleri arar, tam transcription yapmaz → düşük gecikme, düşük CPU.
    """

    def __init__(self, cfg: dict, *, model=None) -> None:
        wake = cfg.get("wake", {})
        self._phrase = wake.get("phrase", WAKE_PHRASE).lower()
        self._model_path = _resolve_local_path(wake.get("vosk_model", "models/vosk-en"))
        self._model = model  # test için inject edilebilir
        self._rec = None

    def _get_recognizer(self):
        if self._rec is not None:
            return self._rec
        try:
            from vosk import Model, KaldiRecognizer
        except ImportError as exc:
            raise RuntimeError(
                "vosk paketi kurulu değil (wake.backend: vosk). Kur: .venv/bin/pip install vosk"
            ) from exc
        import json as _json
        try:
            vosk_model = self._model or Model(self._model_path)
            keywords = _json.dumps([self._phrase, "[unk]"])
            self._rec = KaldiRecognizer(vosk_model, _SAMPLERATE, keywords)
        except Exception as exc:  # noqa: BLE001 — path/format/model yükleme hatası
            raise RuntimeError(
                f"Vosk modeli yüklenemedi: {self._model_path}. "
                "Model klasörünü ve config.wake.vosk_model ayarını kontrol edin."
            ) from exc
        return self._rec

    def wait_for_wake(
        self,
        *,
        stream_factory: Callable = _default_stream,
        stop: Callable[[], bool] = lambda: False,
        triggered: Callable[[], bool] = lambda: False,
        wake_on: Callable[[], bool] = lambda: True,
    ) -> bool:
        import json
        rec = self._get_recognizer()
        with stream_factory() as stream:
            while not stop():
                if triggered():
                    return True
                frame, _ = stream.read(_FRAME)
                if not wake_on():
                    continue
                data = np.asarray(frame).flatten().astype(np.int16).tobytes()
                # YALNIZ final sonuçta tetikle. Partial (yarım) sonuç kullanılmaz:
                # keyword-grammar 'hey' derken sesi erkenden 'hey jarvis'e snap edip
                # partial'da tam phrase üretiyor → 'hey' tek başına yanlış uyandırıyordu.
                if rec.AcceptWaveform(data):
                    text = json.loads(rec.Result()).get("text", "")
                    if self._phrase in text.lower():
                        log.info("wake tetiklendi (vosk): %r", text)
                        rec.Reset()
                        return True
        return False


class ParakeetWakeListener(WhisperWakeListener):
    """NVIDIA Parakeet (parakeet-mlx) tabanlı wake listener.

    WhisperWakeListener'ın sliding-window mantığını AYNEN kullanır; yalnızca
    transkripsiyon backend'i Parakeet'tir. Model ``arda.parakeet.load_model`` ile
    ana STT'yle paylaşılır → tek sefer yüklenir.

    UYARI: 0.6B model sürekli dinlemede whisper-tiny'den AĞIRDIR (CPU/pil). Sürekli
    wake (wake_enabled: true) maliyetli gelirse push-to-talk'ta kal ya da wake için
    whisper-tiny backend'ini kullan; Parakeet'i yalnızca ana STT'de tut.
    """

    def __init__(self, cfg: dict, *, transcribe_fn: Callable | None = None, verify_fn: Callable | None = None) -> None:
        super().__init__(cfg, transcribe_fn=transcribe_fn, verify_fn=verify_fn)
        from arda.constants import PARAKEET_MODEL
        wake = cfg.get("wake", {})
        stt = cfg.get("stt", {})
        # wake'e özel model > stt.parakeet_model > kod varsayılanı
        self._model_repo = wake.get("parakeet_model") or stt.get("parakeet_model") or PARAKEET_MODEL

    def _transcribe(self, audio: np.ndarray) -> str:
        if self._transcribe_fn is not None:  # test enjeksiyonu (parent ile simetrik)
            return self._transcribe_fn(audio)
        from arda.parakeet import load_model, transcribe_array
        try:
            return transcribe_array(load_model(self._model_repo), audio)
        except Exception as exc:  # noqa: BLE001 — wake döngüsü transkripsiyon hatasında çökmemeli
            log.debug("parakeet wake transcribe hatası (yok sayıldı): %s", exc)
            return ""


def make_listener(cfg: dict, *, model=None, transcribe_fn=None):
    """Config'e göre doğru listener döndür.

    wake.backend:
      "vosk"     → VoskWakeListener (offline, doğru)
      "whisper"  → WhisperWakeListener
      "parakeet" → ParakeetWakeListener (NVIDIA Parakeet; İngilizce)
      diğer      → WakeListener (openWakeWord)
    """
    wake = cfg.get("wake", {})
    backend = wake.get("backend", "oww")
    # speaker_verify yalnızca STT-tabanlı backend'lerde (whisper/parakeet) uygulanır.
    # Vosk/OWW ile wake'i açık bırakmak yanlış güvenlik hissi verir; fail-safe davran:
    # listener kurulmasın, trigger katmanı wake'i kapatıp push-to-talk'a düşsün.
    if bool(wake.get("speaker_verify", False)) and backend not in ("whisper", "parakeet"):
        message = (
            f"wake.speaker_verify=true ama '{backend}' backend'i konuşmacı doğrulamayı DESTEKLEMEZ. "
            "Güvenli davranış için wake başlatılmadı; push-to-talk'a düşülüyor. "
            "Doğrulama için wake.backend'i 'whisper' veya 'parakeet' yap."
        )
        log.warning(message)
        raise RuntimeError(message)
    if backend == "vosk":
        return VoskWakeListener(cfg)
    if backend == "whisper":
        return WhisperWakeListener(cfg, transcribe_fn=transcribe_fn)
    if backend == "parakeet":
        return ParakeetWakeListener(cfg, transcribe_fn=transcribe_fn)
    return WakeListener(cfg, model=model)
