# Arda Core MCP Gateway Contract v0.1

Durum: **Read-only profile complete; action boundary implemented but not enabled on Pi**

Bu sözleşme, Nous Research Hermes Agent'in Arda Core'a bağlanacağı sınırı
tanımlar. MCP istemcisi olan Nous Hermes orchestration katmanıdır; Arda Core
ise task truth, policy, evidence, verification ve evaluation authority'sidir.

## Authority invariant'ları

- `Nous Hermes` yalnızca workflow/provider/tool önerisi ve Core request'i üretir.
- `RouteProposal != RouteDecision`.
- `ProviderResult != VerifiedOutcome`.
- MCP tool çağrısı provider, worker, shell veya deployment çağrısı değildir.
- `DENY` veya `REQUIRE_APPROVAL` durumunda provider çağrısı yapılmaz.
- Hermes, Core'un evidence, verification veya evaluation kayıtlarını yazamaz.
- Her çağrı `caller`, `correlation_id` ve uygun durumda `episode_id` taşır.
- Tool descriptor hash'i çağrı provenance'ına bağlanır; descriptor sessizce değiştirilemez.

## Allowlist v0.1

### Read-only tools

| Tool | Zorunlu argüman | Sınır |
| --- | --- | --- |
| `arda_episode_status` | `episode_id` | Episode projection/event evidence okur |
| `arda_verification_status` | `episode_id` | Verification/completion evidence okur |
| `arda_artifact_read` | `artifact_id` | Hash-addressed immutable artifact okur |
| `arda_evaluation_read` | `episode_id` | Evaluation record/projection okur |

### Policy-gated request tools

| Tool | Zorunlu argüman | Sınır |
| --- | --- | --- |
| `arda_task_intake` | `intake_id`, `human_goal`, `task_type`, `objective`, `risk`, `privacy`, `source_ref` | Core validation sonrası Task/Episode intake request'i; execution başlatmaz |
| `arda_route_proposal` | `episode_id`, `task_contract_hash`, `required_capabilities` | Deterministic proposal request'i; provider çağırmaz |
| `arda_request_approval` | `episode_id`, `decision_id`, `approval_payload_hash` | Approval talebi; approval vermez |

`mcp_stdio` varsayılan olarak read-only profilini açar; `--profile action`
yalnız düşük riskli Core request handler'larını ekler. Action profile provider,
worker, shell, approval consume veya execution dispatch etmez.

## Kesinlikle expose edilmeyen yetenekler

- shell/terminal execution
- raw filesystem write/delete
- direct Ollama, Claude, Gemini veya Codex çağrısı
- credential/secret erişimi
- git push, deployment ve production mutation
- evidence/artifact silme veya değiştirme
- verification/evaluation sonucunu değiştirme
- policy veya approval bypass

## Çağrı yaşam döngüsü

```text
Nous Hermes
  -> validated MCP tool intent
  -> Arda Core dispatcher
  -> Task/episode/policy validation
  -> RouteDecision / approval boundary
  -> provider boundary (yalnız Core tarafından)
  -> artifact + verification + evaluation
```

`MCPToolCall` bir execution sonucu değildir; Core dispatcher'a sunulan,
hash'lenmiş ve denetlenebilir bir intent'tir.

## Core-side execution budget

Her stdio gateway process'i tek bir fail-closed execution budget ile başlar:

```yaml
max_tool_calls: 8
max_duration_seconds: 180
```

`--max-tool-calls` ve `--max-duration-seconds` ile daha düşük değerler
verilebilir. Limit aşıldığında çağrı audit edilir ve handler çalıştırılmadan
`isError: true` döner; yeni provider, action veya fallback çağrısı başlatılmaz.
Bu bütçe MCP çağrıları arasındaki loop'u sınırlar. Tek bir handler içindeki
uzun inference'ı kesmez; provider timeout'u ayrıca uygulanır.

## Mevcut transport profilleri

`arda.mcp_server.ReadOnlyMCPGateway` şu JSON-RPC yüzeyini sağlar:

- `initialize`
- `tools/list`
- `tools/call` yalnızca dört read-only tool için
- `notifications/initialized`

Her çağrı `request_hash`, `result_hash`, `caller`, `correlation_id`,
`episode_id` ve sonuç durumuyla audit edilir. Action tool'ları read-only
profilinde dispatch edilmez.

Handler'lar `arda.mcp_read_service.CoreReadService` üzerinden bağlanır;
domain store'larına doğrudan erişmez. Artifact read varsayılan olarak yalnız
metadata/hash döndürür; raw provider içeriği MCP yüzeyine açılmaz.

`arda.mcp_server.ActionMCPGateway` action profilinde dört read tool'a ek olarak
şunları dispatch eder:

- `arda_task_intake`: idempotent Episode/Task intake oluşturur; execution başlatmaz.
- `arda_route_proposal`: yalnız Core route proposal service enjekte edilmişse öneri üretir.
- `arda_request_approval`: pending approval oluşturur; approve/consume yapmaz.

Action handler'ları `arda.mcp_action_service.CoreActionService` üzerinden
bağlanır; MCP katmanı doğrudan domain store sahibi değildir.

## Sonraki gate

1. Action profile için Pi physical gate
2. Idempotent intake ve conflicting payload testi
3. Pending approval provenance ve consume yasağı testi
4. Action profile'ı Nous Hermes'e kontrollü olarak tanıtma
5. DENY, approval, malformed args, timeout ve restart failure matrix
