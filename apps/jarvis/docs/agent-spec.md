# Jarvis Desktop Agent — Teknik Spec

Mevcut güvenli **action** sistemini bozmadan, çok-adımlı bilgisayar/proje komutlarını
planlayıp adım adım yürüten yeni bir **`agent`** intent'i. Tüm yürütme yerel; LLM
(Ollama qwen3:8b) yalnızca **plan üretir**, asla doğrudan shell çalıştırmaz.

## Tasarım kararları (kullanıcı onaylı)
- **Onay UX:** `confirm` riskli adımlar native macOS dialog (osascript) ile onaylanır.
  Çekirdek `confirm: Callable[[str], bool]` enjekte edilir → test'te mock, app'te dialog.
- **Sandbox:** Dosya araçları `agent.workspace` köküne hapsedilir (varsayılan = cwd).
  Path-traversal (`../` ile kök dışına çıkma) engellenir.
- **Intent modeli değişmez:** `agent` intent'inde kullanıcı isteği `Intent.clean_text`'te
  taşınır → `models.Intent` şeması korunur (239 test riski yok).

## Mimari (yeni modüller)
| Modül | Sorumluluk |
|-------|-----------|
| `arda/agent_models.py` | `RiskLevel`, `ToolResult`, `PlanStep`, `Plan`, `StepLog`, `AgentRunResult` |
| `arda/agent_tools.py` | Tool registry + sandbox'lı handler'lar + `ToolContext` |
| `arda/agent_permission.py` | Risk sınıflandırma (auto/confirm/deny), shell whitelist/denylist, sır-yolu denylist |
| `arda/agent_planner.py` | LLM planlayıcı: istek → `Plan` (schema zorlamalı, brain.py kalıbı) |
| `arda/agent_verifier.py` | Adım sonrası doğrulama (write_file → re-read, run_tests → parse) |
| `arda/agent_executor.py` | `run_agent(request)`: plan → permission → confirm → execute → verify → log → özet |
| `arda/agent_audit.py` | her koşuyu (istek + durum + adım izi) günlük markdown'a kalıcılaştırır (denetim) |

Mevcut `arda/agent.py` (dinleme döngüsü) **dokunulmaz** — isim çakışması yok.

## Agent pipeline akışı
```
user request (Intent.clean_text)
  → planner            : LLM, istek → Plan{summary, steps[]}
  → her step için:
      → permission     : classify_risk → auto | confirm | deny
      → deny           : çalıştırma, "reddedildi" logla
      → confirm        : confirm() callback (dialog); hayır → atla
      → execute        : run_tool(name, params, ctx) → ToolResult (asla raise etmez)
      → verify         : verify_step → verified bool
      → log            : StepLog{tool, params, rationale, risk, decision, ok, output, error, verified}
  → spoken summary     : kısa Türkçe özet (TTS) + AgentRunResult{status, spoken, logs}
```

## Tool registry
| Tool | Risk (temel) | Not |
|------|-------------|-----|
| `list_dir` | auto | workspace içi listeleme |
| `read_file` | auto | metin oku (boyut cap); sır-yolu → **deny** |
| `write_file` | confirm | workspace içine yaz |
| `search_files` | auto | dosya adı eşleşmesi (MVP: içerik grep yok → sızıntı yok) |
| `run_tests` | auto | `agent.test_cmd` (vars. `pytest -q`), workspace cwd |
| `safe_shell` | dinamik | whitelist→auto, mutating→confirm, denylist→deny |
| `open_app` | auto | `open -a` |
| `open_url` | confirm | http/https doğrulanır |
| `get_clipboard` | auto | pbpaste |
| `set_clipboard` | confirm | pbcopy |
| `screenshot` | auto (opsiyonel) | `agent.screenshot_enabled` ile; kapalıysa no-op |
| `web_research` | confirm | mevcut research.py + brain.synthesize'a köprü (dış servis); `research_fn` yoksa kapalı |

Tüm IO `ToolContext`'e enjekte edilir (run/clipboard/open/screenshot) → donanımsız test.
`run_tool` asla raise etmez: bilinmeyen/hatalı araç → `ToolResult(ok=False, error=...)`.

## Risk seviyeleri
- **auto:** salt-okuma, listeleme, arama, test çalıştırma → onaysız.
- **confirm:** dosya yazma, paket kurma, git commit, set_clipboard, open_url, dış servise veri.
- **deny:** sır/credential okuma (`.ssh`, `.aws/credentials`, `id_rsa`, `*.pem`, `.env`,
  `.netrc`, keychain, gnupg), destructive delete (`rm -rf`, `dd`, `mkfs`), `sudo`,
  `curl|sh`, izinsiz mail (`mail`/`sendmail`/Mail send) → asla çalıştırılmaz.

safe_shell: `shlex.split` + `shell=False`, cwd=workspace. Önce denylist (regex) → deny;
sonra ilk token allowlist'te → auto; mutating sette → confirm; bilinmeyen → confirm (muhafazakâr).

## Loglama (her adım)
`StepLog` alanları: ne yapmak istedi (`rationale`), hangi tool (`tool`+`params`),
risk (`risk`), karar (`decision`: ran/skipped/denied/confirmed-no/error), çıktı (`output`),
hata (`error`), doğrulandı mı (`verified`). `AgentRunResult.logs` tüm izi taşır.

**Kalıcı denetim** (`agent_audit.py`): her koşu — başarısız/boş dahil — günlük markdown
dosyasına eklenir (`agent.log_dir`, yoksa `vault/AgentRuns/agent-YYYY-MM-DD.md`). Tablo
biçiminde istek + durum + her adımın tool/karar/ok/doğrulama/çıktısı → tam denetlenebilirlik.
Yazım hatası koşuyu bozmaz (executor `_safe_audit` ile yutar).

## Pipeline + router entegrasyonu
- `pipeline.Deps`'e `run_agent: Callable | None = None` eklenir.
- `process_once`: `intent=="agent" and run_agent is not None` → çalıştır → `status="agent"`.
  `run_agent` yoksa → bilinmeyen → inbox (geriye uyumlu).
- `brain._INTENTS = ("answered","action","note","task","agent")` — answered index 0 KORUNUR.
  Prompt'a agent kuralı + örnekler: "bu projeyi incele", "testleri çalıştır hataları özetle"
  = agent; tek Mac eylemi (uygulama aç) = action; açık kayıt = note/task.

## MVP (kabul kriterleri)
1. Proje klasörünü analiz et (list_dir + read_file + search_files).
2. Dosya ara / oku.
3. Test çalıştır → hata özeti.
4. Küçük dosya düzenlemesini **onay isteyerek** yap (write_file → confirm dialog).
5. Sonucu kısa sesli özetle.
6. Büyük hedef: "Jarvis, bu projeyi incele ve ne yapabiliyor söyle" → anlamlı rapor.
7. **Mevcut 239 test yeşil kalır** + yeni modüller test kapsamında.

## Config eklemeleri (config.yaml)
```yaml
agent:
  workspace: null            # null → cwd
  max_steps: 12
  test_cmd: ["pytest", "-q"]
  screenshot_enabled: false
```

## TDD sırası
agent_models → agent_tools → agent_permission → agent_planner → agent_verifier →
agent_executor → pipeline entegrasyonu → router kuralları → tam suite yeşil.
