"""Ollama beyni: ham transkripsiyon → Intent (JSON schema zorlamalı).

Eski hali yalnızca not sınıflandırıcısıydı; artık Siri-benzeri bir YÖNLENDİRİCİ:
not/görev kaydet, soruyu/sohbeti sesli yanıtla (answered), ya da Mac'te kürütörlü
bir EYLEM çalıştır (action + params). Eylemler arda.actions beyaz listesinden gelir.
"""
from __future__ import annotations

import dataclasses
import json
import logging
from datetime import datetime

import ollama

from arda import timeparse
from arda.actions import ACTION_NAMES, ACTION_SPECS
from arda import profile
from arda.constants import ANSWER_TEMPERATURE, ASSISTANT_NAME, LLM_KEEP_ALIVE, LLM_MODEL
from arda.conversation import Turn
from arda.models import Intent

log = logging.getLogger(__name__)


class BrainError(RuntimeError):
    """LLM çağrısı başarısız veya yanıt bozuk/boş. Çağıran (pipeline) bunu yakalayıp
    sesli bir fallback verir → asistan tek bir Ollama hıçkırığıyla susmaz."""


def warm(cfg: dict) -> None:
    """Ollama model(ler)ini açılışta belleğe yükle → ilk komut soğuk-başlatma yaşamasın.

    qwen3:8b ~5 GB; ilk gerçek çağrı modeli diskten yükler (çok-saniyelik stall). Bu,
    her ayrı modele (router_model + answer/model) 1-token'lık bir istek atarak modeli
    keep_alive süresince sıcak tutar. Best-effort: Ollama kapalı/model yoksa startup
    ÇÖKMEZ (STT ısıtmayla aynı kalıp); loglanır ve geçilir."""
    brain = cfg.get("brain", {})
    models = {
        brain.get("router_model") or brain.get("model", LLM_MODEL),
        brain.get("model", LLM_MODEL),
    }
    client = ollama.Client(host=brain.get("host"))
    keep_alive = brain.get("keep_alive", LLM_KEEP_ALIVE)
    for model in models:
        try:
            client.chat(
                model=model,
                messages=[{"role": "user", "content": "ping"}],
                think=brain.get("think", False),
                options={"num_predict": 1},
                keep_alive=keep_alive,
            )
        except Exception as exc:  # noqa: BLE001 — ısıtma best-effort; startup'ı çökertmesin
            log.warning("LLM ısıtma başarısız (%s, yok sayıldı): %s", model, exc)


# Router/cevap çağrılarına gönderilecek en fazla tur sayısı. Sohbet belleği zaten
# sınırlı (Conversation.max_turns) ama burada da kesiyoruz: uzun bağlam = yüksek
# gecikme, sesli asistanda gecikme ölümcül (sliding window).
_MAX_HISTORY_TURNS = 8

_ACTION_LIST = "\n".join(f"  - {name}: {desc}" for name, desc in ACTION_SPECS.items())

# Schema ile prompt'un tek kaynaktan beslenmesi için enum'ları burada tanımla.
# Sıra "answered" ile başlar → kısıtlı çıktıda model belirsizlikte sohbete meyletsin,
# not arşivlemeye değil (bkz. ÖNCELİK kuralı + few-shot).
_INTENTS = ("answered", "action", "note", "task", "research", "agent")
_CATEGORIES = ("internship", "research", "idea", "daily")
_SYSTEM_PROMPT = f"""\
Sen {ASSISTANT_NAME}'sin — Siri benzeri bir masaüstü sesli asistan. Kullanıcının
sözlü kaydını analiz et ve TEK bir JSON nesnesi döndür. Önce ne istendiğini anla,
sonra 'intent' alanını şuna göre seç:

DİL: clean_text alanını DAİMA kullanıcının konuştuğu dilde yaz. Kullanıcı Türkçe
konuştuysa Türkçe, İngilizce konuştuysa İngilizce yanıt ver. Diğer alanlar
(category, action, tags vb.) iç kullanım içindir, dilden bağımsızdır.

ÖNCELİK (ÇOK ÖNEMLİ): Bu bir SOHBET asistanıdır. Kullanıcı AÇIK bir kayıt fiili
(not al, kaydet, yaz, ekle, "şunu not et", "yapılacaklara ekle") kullanmadıysa girdi
"answered"dır. Selamlaşma, soru, muhabbet ve his/durum/fikir bildiren cümleler
("bugün yorgunum", "film izlemek istiyorum", "canım sıkkın") DAİMA "answered" — bunlar
NOT DEĞİLDİR. Belirsizsen "answered" seç; ASLA varsayılan olarak "note"/"task"a kaçma.

- "answered": selam/soru/sohbet/muhabbet veya his-durum-fikir bildiren her cümle
  (açık kayıt isteği yoksa). Cevabını DOĞRUDAN 'clean_text'e yaz (kısa, doğal, kullanıcı dilinde).
- "action": kullanıcı bir şey YAPMANI istiyorsa (uygulama aç, ses ayarla, hatırlat...).
  'action'a listeden tam adı, 'params'a değerleri yaz.
- "note": YALNIZCA kullanıcı açıkça kaydetmeni/not almanı istediğinde
  (ör. "şunu not al: süt almayı unutma"). 'category' + 'title' + 'clean_text'.
- "task": YALNIZCA kullanıcı açıkça bir görev/yapılacak eklemeni istediğinde
  (ör. "şunu yapılacaklara ekle: sunumu bitir"). 'title' görevi özetlesin.
- "research": kullanıcı AÇIKÇA araştırma istediğinde ('araştır', 'internetten bak', 'web'de ara')
  VEYA cevap GÜNCEL/CANLI web bilgisi gerektirdiğinde (bugünkü haber/fiyat/skor/güncel olay).
  'clean_text' = kısa-net ARAMA SORGUSU. Genel bilgi/sohbet/fikir → research DEĞİL, "answered".
- "agent": kullanıcı YEREL bilgisayar/proje üzerinde ÇOK ADIMLI bir iş istediğinde
  (projeyi/klasörü incele, dosya ara-oku-düzenle, testleri çalıştır ve hataları özetle,
  kod/dosyalar üzerinde işlem yap). Tek basit Mac eylemi (uygulama aç, ses ayarla) → "action";
  güncel/web bilgisi → "research". 'clean_text' = kullanıcının TAM isteği (kullanıcının dilinde).

Örnekler:
  "naber" -> answered
  "bugün çok yorgunum" -> answered (his bildirimi; kayıt değil)
  "film izlemek istiyorum" -> answered (sohbet; 'ekle/not al' yok)
  "saat kaç" -> action (tell_time)
  "şunu not al: süt almayı unutma" -> note
  "bunu yapılacaklara ekle: sunumu bitir" -> task
  "yarın 8'de dersim var, hatırlat" -> action (set_reminder)
  "yapay zeka haberlerini araştır" -> research (clean_text="yapay zeka haberleri")
  "bugün dolar kaç oldu, internetten bak" -> research (clean_text="bugün dolar kuru")
  "bu projeyi incele ve ne yapabildiğini söyle" -> agent (clean_text=tam istek)
  "testleri çalıştır ve hataları özetle" -> agent (clean_text="testleri çalıştır ve hataları özetle")

Kullanılabilir eylemler (yalnızca bu adlar geçerli):
{_ACTION_LIST}

Alanlar:
- intent: "answered" | "action" | "note" | "task" | "research" | "agent"
- category: içeriğin alanı. Şunlardan biri: internship | research | idea | daily.
  Emin değilsen "daily" kullan.
- title: kısa başlık (max 60 karakter, kullanıcının dilinde)
- clean_text: not için düzeltilmiş metin; answered için SESLİ YANIT metni
  (kullanıcının dilinde)
- tags: anahtar kelimeler (yoksa boş liste)
- follow_up: sonradan bakılacaklar (yoksa boş liste)
- action: intent=="action" ise eylem adı, değilse ""
- params: eylem parametreleri (string değerler), değilse boş nesne
- confidence: "high" | "low". Hangi niyet/eylem olduğundan ya da parametrelerden
  (özellikle geri-alınamaz bir eylem: hatırlatıcı sil, ses/ekran) EMİN DEĞİLSEN "low".
  Girdi net ve tek anlamlıysa "high". Şüphedeysen "low" (asistan onay isteyecek).
- mem_durable: kullanıcı KALICI bir gerçek paylaştıysa true (adı, tercihi, ilişkisi,
  bir karar, proje durumu). Geçici komut/soru/sohbet → false.
- mem_scope: kalıcı gerçek KİŞİSELSE "personal" (ad, tercih, ilişki); İŞE/GÖREVE
  özelse "jarvis" (proje, yapılacak iş). Emin değilsen "personal".

Önceki konuşma turları bağlam olarak verilebilir. Takip sorularını ve atıfları buna
göre çöz (ör. "peki yarın?", "biraz daha aç", "onu da not et" → önceki turla ilişkilendir).
"""

_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(_INTENTS)},
        "category": {"type": "string", "enum": list(_CATEGORIES)},
        "title": {"type": "string"},
        "clean_text": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "follow_up": {"type": "array", "items": {"type": "string"}},
        "action": {"type": "string", "enum": ["", *ACTION_NAMES]},
        "params": {
            "type": "object",
            "additionalProperties": {"type": "string"},
        },
        "confidence": {"type": "string", "enum": ["high", "low"]},
        "mem_durable": {"type": "boolean"},
        "mem_scope": {"type": "string", "enum": ["personal", "jarvis"]},
    },
    "required": [
        "intent",
        "category",
        "title",
        "clean_text",
        "tags",
        "follow_up",
        "action",
        "params",
        "confidence",
    ],
}


# Serbest sohbet (answered) için AYRI sistem promptu: schema yok, doğal konuşma.
# Router'ın schema+düşük-sıcaklık kafesi naturalness'i kısırlaştırır; cevap motoru
# bu yüzden ikinci, schema'sız bir çağrıdır (bkz. answer()).
_ANSWER_SYSTEM_PROMPT = f"""\
Sen {ASSISTANT_NAME}'sin — zeki, samimi, konuşkan bir sesli asistan. Kullanıcıyla DOĞAL ve sıcak konuş.
Yanıtın sesli okunacak: sade, düz konuşma metni (markdown/madde işareti ASLA kullanma,
tire tire veya yıldız koyma). Cevaplarını tam ve doyurucu yap — soruyu düzgünce açıkla,
gerekirse örnek ver, bağlam kur. Tek cümleyle geçiştirme; konuyu gerçekten anlat.
Kullanıcı hangi dilde konuştuysa o dilde yanıtla. Önceki turlar bağlamdır; takip sorularını
ve atıfları ("peki yarın?", "biraz daha anlat") ona göre çöz.
"""

_TR_GUNLER = ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar")


def _answer_system(now: "Callable[[], datetime]") -> str:
    """Sohbet cevabı sistem promptu + ŞU ANKİ tarih/gün/saat. Model 'bugün ne',
    'günlerden ne', 'saat kaç' gibi soruları UYDURMASIN; verilen tarihi kullansın."""
    dt = now()
    stamp = dt.isoformat(timespec="minutes")
    gun = _TR_GUNLER[dt.weekday()]
    return (
        f"{_ANSWER_SYSTEM_PROMPT}\n"
        f"Şu anki gerçek yerel tarih-saat (ISO 8601): {stamp}. Bugün {gun}.\n"
        "Tarih/gün/saat sorulursa BU bilgiyi kullan, ASLA uydurma."
    )


def _action_summary(ar: dict) -> str:
    """Yapılandırılmış action sonucunu modele okunabilir tek satıra çevir.

    Başarısızsa açıkça 'BAŞARISIZ' yaz (model 'kuramamıştım' bağlamını bilsin); aksi
    halde action adı + anlamlı alanlar (text/at/id…) → 'az önce kurduğum'u çözebilsin."""
    action = ar.get("action", "eylem")
    if not ar.get("success", True):
        return f"{action} BAŞARISIZ"
    parts = [f"{k}={v!r}" for k, v in ar.items() if k not in ("action", "success") and v not in (None, "")]
    return f"{action} → " + ", ".join(parts) if parts else action


def _build_messages(
    text: str, history: tuple[Turn, ...], *, system: str = _SYSTEM_PROMPT,
    memories: tuple[str, ...] = (),
) -> list[dict]:
    """system → (son N tur) → güncel kullanıcı sözü sırasıyla mesajları kur.

    Geçmiş, beynin takip sorularını/atıfları çözmesi için bağlamdır; boşsa davranış
    eski tek-tur haliyle birebir aynıdır. En fazla son `_MAX_HISTORY_TURNS` tur
    gönderilir (sliding window) → uzun oturumda bağlam şişmez, gecikme artmaz.

    Bir turda action_result varsa, asistan mesajına okunabilir bir '[eylem: …]' eki
    iliştirilir → bu ek SESLİ OKUNMADI (sadece bellekte), ama hem router hem answer
    motoru görür, böylece sonraki turda eyleme atıf çözülür.

    `memories` doluysa (mem0'dan geri çağrılan hatırlanan gerçekler), sistem mesajına
    'Hatırladıkların' bloğu iliştirilir; boşsa (varsayılan) davranış birebir aynıdır."""
    if memories:
        recall_block = "\n".join(f"- {m}" for m in memories)
        system = f"{system}\n\nHatırladıkların (uygunsa kullan, uydurma):\n{recall_block}"
    messages: list[dict] = [{"role": "system", "content": system}]
    for turn in tuple(history)[-_MAX_HISTORY_TURNS:]:
        messages.append({"role": "user", "content": turn.user})
        content = turn.assistant
        if turn.action_result:
            content = f"{content}\n[eylem: {_action_summary(turn.action_result)}]"
        messages.append({"role": "assistant", "content": content})
    messages.append({"role": "user", "content": text})
    return messages


def _router_system(now: "Callable[[], datetime]") -> str:
    """Router sistem promptu + ŞU ANKİ saat. Model göreli zamanları (yarın 8'de,
    2 saat sonra) bu ana göre mutlak ISO 8601'e çevirip set_reminder.at'e yazsın."""
    stamp = now().isoformat(timespec="minutes")
    return (
        f"{_SYSTEM_PROMPT}\n\n"
        f"Şu anki yerel tarih-saat (ISO 8601): {stamp}.\n"
        "Hatırlatma istenirse (ör. 'yarın 8'de toplantı hatırlat') intent=action,\n"
        "action=set_reminder; params.at = bu ana göre hesaplanmış MUTLAK ISO 8601\n"
        "tarih-saat (örn. 2026-06-20T08:00), params.text = hatırlatılacak şey.\n"
        "Önceki bir hatırlatıcıya atıf yapılırsa ('az önce kurduğum', 'onu sil', 'paneli sil')\n"
        "geçmişteki [eylem: set_reminder …] bağlamından başlığı (ve varsa id'yi) al →\n"
        "action=delete_reminder, params.title (+ params.id). 'Hatırlatıcılarım ne' → list_reminders."
    )


def classify(
    text: str,
    *,
    cfg: dict,
    history: tuple[Turn, ...] = (),
    now: "Callable[[], datetime]" = datetime.now,
) -> Intent:
    """Ham transkripsiyon metnini Ollama ile Intent'e çevirir. Başarısızsa raise eder.

    history verilirse önceki turlar bağlam olarak gönderilir (çok-turlu sohbet).
    `now` router prompt'una enjekte edilir → göreli zamanlar mutlak ISO'ya çözülür.
    cfg.brain.host yapılandırılmışsa o sunucuya bağlanır (uzak/özel Ollama)."""
    brain = cfg.get("brain", {})
    # Router için ayrı (küçük/hızlı) model olabilir; yoksa ana modele düş. classify
    # düşük-sıcaklık + JSON şema ile çalışır → küçük model genelde yeterli, gecikme düşer.
    router_model = brain.get("router_model") or brain.get("model", LLM_MODEL)
    client = ollama.Client(host=brain.get("host"))
    response = client.chat(
        model=router_model,
        messages=_build_messages(
            text, history,
            system=_router_system(now) + profile.about_block(cfg) + profile.command_examples(cfg),
        ),
        format=_SCHEMA,
        think=brain.get("think", False),
        options={"temperature": 0.1},
        keep_alive=brain.get("keep_alive", LLM_KEEP_ALIVE),
    )
    parsed = json.loads(response.message.content)
    return _resolve_action_time(Intent.from_dict(parsed), text, now)


def _resolve_action_time(intent: Intent, text: str, now: "Callable[[], datetime]") -> Intent:
    """set_reminder.at'i ham metinden DETERMİNİSTİK göreli-zaman ile düzelt (varsa).

    Belirsiz-olmayan ifadeler ('10 dakika sonra', 'yarın akşam 8') LLM'in `at` değerini
    ezer (LLM aritmetiği/biçimi güvenilmez); tanınan sinyal yoksa LLM korunur. Intent
    frozen → değişiklikte yeni kopya döndürülür (immutability)."""
    if intent.action != "set_reminder":
        return intent
    iso = timeparse.resolve_when(text, now=now(), fallback_iso=intent.params.get("at"))
    if not iso or iso == intent.params.get("at"):
        return intent
    return dataclasses.replace(intent, params={**intent.params, "at": iso})


def answer(
    text: str, *, cfg: dict, history: tuple[Turn, ...] = (),
    memories: tuple[str, ...] = (),
    now: "Callable[[], datetime]" = datetime.now,
) -> str:
    """Serbest, schema'sız, daha yüksek sıcaklıkta DOĞAL sohbet cevabı (router'dan AYRI).

    Router (classify) niyeti/eylemi düşük sıcaklık + schema ile belirler; intent
    "answered" olduğunda asıl cevap BURADAN gelir → JSON kafesi doğallığı bozmaz.
    Sıcaklık cfg.brain.answer_temperature ile ayarlanır (varsayılan 0.7).

    `memories` doluysa mem0'dan geri çağrılan hatırlanan gerçekler sistem promptuna
    iliştirilir (bkz. `_build_messages`); boşsa davranış birebir aynıdır.

    Hata/boş yanıt → BrainError (pipeline router'ın clean_text'ine düşer; asistan
    asla sessiz kalmaz)."""
    brain = cfg.get("brain", {})
    try:
        client = ollama.Client(host=brain.get("host"))
        response = client.chat(
            model=brain.get("model", LLM_MODEL),
            messages=_build_messages(
                text, history,
                system=_answer_system(now) + profile.about_block(cfg), memories=memories,
            ),
            think=brain.get("think", False),
            options={"temperature": brain.get("answer_temperature", ANSWER_TEMPERATURE)},
            keep_alive=brain.get("keep_alive", LLM_KEEP_ALIVE),
        )
        content = (response.message.content or "").strip()
    except Exception as exc:  # noqa: BLE001 — her LLM/ağ hatası BrainError'a sarılır
        raise BrainError(f"Cevap çağrısı başarısız: {exc}") from exc
    if not content:
        raise BrainError("Beyin boş cevap döndürdü")
    return content


def answer_stream(
    text: str, *, cfg: dict, history: tuple[Turn, ...] = (),
    memories: tuple[str, ...] = (),
    now: "Callable[[], datetime]" = datetime.now,
) -> "Iterator[str]":
    """answer() ile aynı (serbest, schema'sız, yüksek sıcaklık) ama AKAN: yanıtı
    token parçaları (str) olarak yield eder → çağıran speak.iter_sentences ile cümle
    cümle seslendirip ilk-sese-kadar gecikmeyi düşürebilir (Batch 1).

    `memories` doluysa mem0'dan geri çağrılan hatırlanan gerçekler sistem promptuna
    iliştirilir (bkz. `_build_messages`); boşsa davranış birebir aynıdır.

    Bağlantı/ilk-çağrı hatası BrainError'a sarılır (jeneratör tüketilmeye başlandığında
    yükselir; pipeline tam cevaba düşer → asistan asla sessiz kalmaz). Boş parçalar atlanır."""
    brain = cfg.get("brain", {})
    try:
        client = ollama.Client(host=brain.get("host"))
        stream = client.chat(
            model=brain.get("model", LLM_MODEL),
            messages=_build_messages(
                text, history,
                system=_answer_system(now) + profile.about_block(cfg), memories=memories,
            ),
            think=brain.get("think", False),
            options={"temperature": brain.get("answer_temperature", ANSWER_TEMPERATURE)},
            keep_alive=brain.get("keep_alive", LLM_KEEP_ALIVE),
            stream=True,
        )
        for part in stream:
            piece = part.message.content or ""
            if piece:
                yield piece
    except Exception as exc:  # noqa: BLE001 — her LLM/ağ hatası BrainError'a sarılır
        raise BrainError(f"Cevap akışı başarısız: {exc}") from exc


# Web araştırma sentezi için KAYNAK-KISITLI prompt: model yalnızca verilen kaynaklardan
# üretmeli, uydurmamalı, önemli iddialara [n] atıfı koymalı (kaynaklı not için).
_SYNTHESIZE_SYSTEM_PROMPT = f"""\
Sen {ASSISTANT_NAME}'sin — bir araştırma asistanı. YALNIZCA aşağıda verilen numaralı kaynaklara
dayanarak yanıtla. Kaynaklarda OLMAYAN bir bilgiyi UYDURMA; yeterli bilgi yoksa bunu
açıkça söyle. Önemli her iddianın sonuna kaynak numarasını koy: [1], [2]. Kısa ve net
yaz, kullanıcının sorusuyla aynı dilde (markdown/madde işareti YOK)."""


def _format_sources(sources) -> str:
    """Kaynakları numaralı (1..n) bağlam metnine çevir — sentez promptuna eklenir."""
    return "\n\n".join(
        f"[{i}] {s.title} — {s.url}\n{s.snippet}" for i, s in enumerate(sources, 1)
    )


def synthesize(query: str, sources, *, cfg: dict) -> str:
    """Arama kaynaklarından KAYNAK-KISITLI, [n] atıflı kısa cevap üret (schema yok).

    Hata/boş yanıt → BrainError (pipeline sesli fallback verir). Cevap metni [n]
    atıfları içerir (kaynaklı not için); sesli okumadan önce research.spoken_summary
    ile atıflar sökülüp kısaltılır."""
    brain = cfg.get("brain", {})
    user_msg = f"Soru: {query}\n\nKaynaklar:\n{_format_sources(sources)}"
    try:
        client = ollama.Client(host=brain.get("host"))
        response = client.chat(
            model=brain.get("model", LLM_MODEL),
            messages=[
                {"role": "system", "content": _SYNTHESIZE_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            think=brain.get("think", False),
            options={"temperature": brain.get("answer_temperature", ANSWER_TEMPERATURE)},
            keep_alive=brain.get("keep_alive", LLM_KEEP_ALIVE),
        )
        content = (response.message.content or "").strip()
    except Exception as exc:  # noqa: BLE001 — her LLM/ağ hatası BrainError'a sarılır
        raise BrainError(f"Sentez çağrısı başarısız: {exc}") from exc
    if not content:
        raise BrainError("Beyin boş sentez döndürdü")
    return content