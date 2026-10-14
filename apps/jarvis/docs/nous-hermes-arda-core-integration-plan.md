# Nous Hermes Agent → Arda Core Integration Plan

Durum: **INSTALLATION COMPLETE / MCP CONTRACT COMPLETE / STANDALONE PROVIDER BLOCKED**

## Terminology boundary

- **Nous Hermes Agent:** kullanıcıya yakın agent/gateway; CLI, messaging,
  session, memory, skills ve provider/tool yüzeyi.
- **Arda Core:** TaskContract, Episode, policy, approval, provider boundary,
  verification, artifacts ve evaluation authority.
- **arda.hermes_daemon:** Arda Core içindeki önceki coordinator prototipi;
  Nous Hermes Agent değildir.

Mevcut custom `hermes.service` bu prototipe aittir ve Nous Agent kurulumu ile
aynı servis olarak değerlendirilmemelidir.

## Resmi entegrasyon bulguları

Nous Hermes Agent Linux/aarch64 için desteklenen platformlar arasında yer alıyor.
Resmi installer `install.sh` ile yönetilen kurulum ve ayrı bir Hermes home
kullanıyor; PyPI/pip kurulum yöntemi resmi destek yolu olarak belirtilmiyor.

Hermes dış MCP server'ları stdio veya HTTP üzerinden yükleyebiliyor. Bu nedenle
ilk Arda entegrasyonu için doğrudan model/provider adapter yerine loopback/local
MCP gateway seçilecek.

## Pi kurulum sonucu

- Nous Hermes Agent `v0.21.5+5778.g0a374d1` kuruldu.
- Kaynak: `/opt/arda-suite/apps/jarvis`.
- Ayrı home: `/var/lib/arda-suite/hermes`.
- Kurulum commit'i: `0a374d167`.
- Browser, OAuth/setup, MCP ve messaging gateway açılmadı.
- Resmi generated launcher Pi runtime'ında `ruamel.yaml` import edemedi; vendor
  dosyalarına dokunulmadan venv tabanlı `/opt/arda-suite/bin/hermes-nous`
  launcher'ı kullanıldı.
- CLI version/help smoke: PASS.
- Standalone local Ollama smoke: **BLOCKED**. Mevcut `qwen3:1.7b` modelinin
  bildirilen context window'u `40,960`; Hermes Agent minimum `64,000` istiyor.
  Bu nedenle context gereksinimi gevşetilmedi ve yeni model indirilmedi.

## Hedef akış

```text
Telegram / Discord / CLI
          |
          v
Nous Hermes Agent
          |
          | allowlisted MCP tools only
          v
Arda Core MCP Gateway
          |
          v
TaskContract -> Episode -> Policy -> Provider Boundary
          |
          v
Verification -> Evaluation
```

## İlk MCP yüzeyi

Read-only:

- `arda_episode_status`
- `arda_verification_status`
- `arda_artifact_read`
- `arda_evaluation_read`

Policy-gated actions:

- `arda_task_intake`
- `arda_route_proposal`
- `arda_request_approval`

İlk sürümde Hermes'e expose edilmeyecekler:

- shell/terminal;
- raw filesystem write;
- direct Ollama veya cloud provider;
- credentials/secrets;
- git push/deployment;
- evidence, verification veya evaluation mutation.

## Güvenlik invariants

```text
Hermes tool call != Core approval
RouteProposal != RouteDecision
ProviderResult != VerifiedOutcome
```

MCP handler'ları yalnızca typed request üretmeli veya read-only projection
döndürmeli. Gerçek provider çağrısı yalnızca Core policy `ALLOW` sonrasında,
Core-owned provider boundary üzerinden yapılmalı.

MCP allowlist ve çağrı doğrulaması `arda.mcp_contract` içinde kodlanmıştır;
canlı MCP transport/dispatch henüz açılmamıştır. Route request'leri ayrıca
provider capability metadata'sından `min_context_window` şartı taşıyabilir.
Bu nedenle Hermes'in 64K minimumu, context window'u 40,960 olan
`qwen3:1.7b` provider'ını uygun aday olarak kabul etmez.

## Kurulum sırası

1. Pi mimarisi, Python, glibc/systemd ve boş port/env audit.
2. Nous resmi installer'ını ayrı Hermes home ile çalıştırma.
3. `hermes --version` ve standalone `hermes chat -q` smoke testi.
4. Toolset'leri minimum profile'a indirme; terminal/file/browser kapalı.
5. Arda Core MCP Gateway v0.1 contract ve read-only health testleri.
6. Hermes'e yalnız allowlist MCP server bağlama.
7. `arda_task_intake` ile gerçek olmayan fixture task açma.
8. Policy DENY / approval / unavailable / malformed response failure matrix.
9. Ancak bundan sonra Telegram/Discord gateway.

## Kabul kapıları

- Standalone Nous Hermes PASS.
- Hermes doğrudan provider veya shell çağıramıyor.
- MCP gateway yalnız loopback/local erişilebilir.
- DENY durumunda provider çağrısı yok.
- Approval olmadan action ilerlemiyor.
- Hermes restart sonrası session durumu korunuyor.
- Arda Core Episode/Evidence/Evaluation zinciri oluşuyor.
- Secret veya raw credential Hermes tool payload'ına girmiyor.
- Mevcut Arda Core custom coordinator ile servis isimleri ayrışıyor.

## Karar

Önce Nous Hermes Agent standalone kurulur ve doğrulanır. MCP gateway bağlantısı
standalone smoke geçmeden açılmaz. Arda Core authority'si Nous Agent'ın tool,
memory veya provider özelliklerine devredilmez.
