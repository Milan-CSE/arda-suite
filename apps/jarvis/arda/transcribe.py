"""STT: WAV → metin. Backend seçilebilir (config.stt.backend):

  "whisper"  → mlx-whisper (varsayılan, Türkçe dahil çok dilli).
  "parakeet" → NVIDIA Parakeet (parakeet-mlx); İngilizce için daha hızlı/doğru,
               wake listener'ın parakeet backend'iyle AYNI modeli paylaşır.

Her iki transcriber da aynı arayüzü uygular: ``transcribe(wav) -> str`` ve ``warm()``.
``make_transcriber(cfg)`` config'e göre doğru olanı döndürür (wake.make_listener ile simetrik).
"""
from __future__ import annotations

import logging
import json
import tempfile
import threading
import wave
from pathlib import Path

try:
    import mlx_whisper
except ImportError:  # Raspberry Pi/Linux can use another STT adapter later.
    class _MissingMLX:
        @staticmethod
        def transcribe(*_args, **_kwargs):
            raise RuntimeError(
                "mlx-whisper bu platformda kurulu değil; STT backend'ini desteklenen bir adapter ile yapılandır"
            )

    mlx_whisper = _MissingMLX()

# mlx_whisper thread-safe değil: eş zamanlı çağrılar deadlock'a giriyor.
# Tüm transcribe() çağrıları (warm dahil) bu kilit altında sıralanır.
_MLX_LOCK = threading.Lock()
# Kilit edinme üst sınırı: önceki bir transcribe TAKILIRSA (kilidi bırakmazsa), yeni
# çağrı SONSUZA dek bloklanmasın → sınırlı bekleyip TimeoutError'la başarısız ol. Normal
# transcribe (saniyeler) bu sürenin çok altında; bu yalnız takılma için emniyet sübabı.
_LOCK_ACQUIRE_TIMEOUT = 120.0

from arda import profile
from arda.constants import PARAKEET_MODEL, STT_MODEL
from arda.hallucination import _DEFAULT_SILENCE_RMS, is_hallucination, is_silent

log = logging.getLogger(__name__)
_VOSK_MODELS: dict[str, object] = {}


def _resolve_local_model(value: str) -> str:
    path = Path(value).expanduser()
    return str(path if path.is_absolute() else Path(__file__).resolve().parents[1] / path)


def transcribe_mlx(
    audio_or_path, *, model: str, language: str | None,
    lock_timeout: float = _LOCK_ACQUIRE_TIMEOUT, initial_prompt: str | None = None,
):
    """mlx_whisper çağrısını tek, thread-safe kapıdan geçir (takılma-güvenli).

    `mlx_whisper.transcribe` dosya yolu ya da numpy audio array kabul eder. Ana STT,
    dikte ve Whisper wake aynı process içinde çalışabildiği için çağrılar bu global
    kilit altında sıralanır.

    Kilit `lock_timeout` ile edinilir: önceki bir çağrı takılıp kilidi tutuyorsa, `with`
    sonsuza dek bloklardı (dikte timeout'u daemon thread'i bırakır ama mlx çağrısı kilidi
    elde tutmaya devam eder → tüm sonraki whisper STT kalıcı kilitlenirdi). Sınırlı edinme
    + finally release → bir takılma sistemi kalıcı deadlock'a sokmaz; yeni çağrı net hata alır.
    """
    if not _MLX_LOCK.acquire(timeout=lock_timeout):
        raise TimeoutError(
            f"STT kilidi {lock_timeout:g} sn'de alınamadı (önceki transcribe takılmış olabilir)"
        )
    try:
        kwargs = {"path_or_hf_repo": model, "language": language}
        if initial_prompt:
            # Whisper'ı isimler/terimler yönünde önyükle (profile.vocabulary) → özel
            # isimler ('Sample User', proje adları) doğru yazılır. Boşsa hiç geçme (varsayılan).
            kwargs["initial_prompt"] = initial_prompt
        return mlx_whisper.transcribe(audio_or_path, **kwargs)
    finally:
        _MLX_LOCK.release()


class Transcriber:
    def __init__(self, cfg: dict) -> None:
        stt = cfg.get("stt", {})
        self._model = stt.get("model", STT_MODEL)
        self._language = stt.get("language", "tr")
        self._silence_rms = float(stt.get("silence_rms", _DEFAULT_SILENCE_RMS))
        self._initial_prompt = profile.vocabulary(cfg) or None   # isim/terim önyüklemesi (yoksa None)

    def transcribe(self, wav: Path) -> str:
        """WAV dosyasını metne çevirir. Boşlukları temizler, başarısızsa raise eder.

        SESSİZLİK GEÇİDİ: kayıt enerjisi düşükse (konuşma yok) whisper'a hiç gitmez →
        'İzlediğiniz için teşekkür ederim' gibi sessizlik-uydurması önlenir. Enerji geçse
        bile bilinen uydurma kalıpları ayrıca elenir (bkz. arda.hallucination).

        profile.vocabulary varsa whisper'a initial_prompt olarak geçer → özel isimler doğru yazılır."""
        if is_silent(wav, self._silence_rms):
            return ""
        result = transcribe_mlx(
            str(wav), model=self._model, language=self._language,
            initial_prompt=self._initial_prompt,
        )
        text = result["text"].strip()
        return "" if is_hallucination(text) else text

    def warm(self) -> None:
        """Modeli açılışta önceden yükle (ilk gerçek kayıt yavaş olmasın).

        Best-effort: kısa bir sessiz WAV transkribe eder. İndirme/yükleme
        hatası startup'ı çökertmemeli; loglanır ve geçilir."""
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                silent = Path(tmp.name)
            with wave.open(str(silent), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(b"\x00\x00" * 1600)  # ~0.1 sn sessizlik
            try:
                # Geçidi BAYPAS et: warm sessiz WAV kullanır; is_silent onu atlarsa model
                # hiç yüklenmez. Doğrudan transcribe_mlx → model gerçekten ısınır.
                transcribe_mlx(str(silent), model=self._model, language=self._language)
            finally:
                silent.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001 — ısıtma best-effort
            log.warning("Model ısıtma başarısız (yok sayıldı): %s", exc)


class ParakeetTranscriber:
    """NVIDIA Parakeet (parakeet-mlx) ile STT — Apple Silicon, in-process, sıcak model.

    ``Transcriber`` ile aynı arayüz. Model ``arda.parakeet.load_model`` ile paylaşılır →
    wake listener parakeet backend'iyle tek instance (RAM iki kopya tutmaz).
    """

    def __init__(self, cfg: dict) -> None:
        stt = cfg.get("stt", {})
        self._model = stt.get("parakeet_model", PARAKEET_MODEL)
        self._silence_rms = float(stt.get("silence_rms", _DEFAULT_SILENCE_RMS))

    def transcribe(self, wav: Path) -> str:
        """WAV dosyasını metne çevirir. Sessizlik geçidi + uydurma filtresi (bkz. Transcriber)."""
        if is_silent(wav, self._silence_rms):
            return ""
        from arda.parakeet import load_model, transcribe_file

        text = transcribe_file(load_model(self._model), wav)
        return "" if is_hallucination(text) else text

    def warm(self) -> None:
        """Modeli açılışta önceden yükle. Best-effort: indirme/yükleme hatası startup'ı çökertmez."""
        try:
            from arda.parakeet import load_model

            load_model(self._model)
        except Exception as exc:  # noqa: BLE001 — ısıtma best-effort
            log.warning("Parakeet model ısıtma başarısız (yok sayıldı): %s", exc)


class VoskTranscriber:
    """Offline Vosk STT adapter for Raspberry Pi/Linux deployments."""

    def __init__(self, cfg: dict) -> None:
        stt = cfg.get("stt", {})
        self._model_path = _resolve_local_model(stt.get("vosk_model", "models/vosk-en"))
        self._silence_rms = float(stt.get("silence_rms", _DEFAULT_SILENCE_RMS))

    def _model(self):
        model = _VOSK_MODELS.get(self._model_path)
        if model is None:
            try:
                from vosk import Model
            except ImportError as exc:
                raise RuntimeError(
                    "vosk kurulu değil; RPi kurulumu için .venv/bin/pip install vosk çalıştır"
                ) from exc
            model = Model(self._model_path)
            _VOSK_MODELS[self._model_path] = model
        return model

    def transcribe(self, wav: Path) -> str:
        if is_silent(wav, self._silence_rms):
            return ""
        try:
            from vosk import KaldiRecognizer
        except ImportError as exc:
            raise RuntimeError(
                "vosk kurulu değil; RPi kurulumu için .venv/bin/pip install vosk çalıştır"
            ) from exc
        with wave.open(str(wav), "rb") as source:
            if source.getnchannels() != 1 or source.getsampwidth() != 2:
                raise RuntimeError("Vosk STT mono 16-bit WAV bekliyor")
            recognizer = KaldiRecognizer(self._model(), source.getframerate())
            while chunk := source.readframes(4000):
                recognizer.AcceptWaveform(chunk)
            result = json.loads(recognizer.FinalResult() or "{}")
        text = str(result.get("text") or "").strip()
        return "" if is_hallucination(text) else text

    def warm(self) -> None:
        try:
            self._model()
        except Exception as exc:  # noqa: BLE001 — startup best-effort
            log.warning("Vosk model ısıtma başarısız (yok sayıldı): %s", exc)


def make_transcriber(cfg: dict):
    """config.stt.backend'e göre transcriber döndür.

      "parakeet" → ParakeetTranscriber (NVIDIA Parakeet)
      "vosk" → VoskTranscriber (offline Raspberry Pi/Linux)
      diğer      → Transcriber (mlx-whisper, varsayılan)
    """
    backend = cfg.get("stt", {}).get("backend", "whisper")
    if backend == "vosk":
        return VoskTranscriber(cfg)
    if backend == "parakeet":
        return ParakeetTranscriber(cfg)
    return Transcriber(cfg)
