# ARDA Suite

ARDA Suite brings the local-first Jarvis assistant, the experimental ARDA Core/Hermes runtime, and the optional Shared Memory service into one repository. It keeps them as separate components with independent Python environments and deployment lifecycles.

## Components

| Component | Path | Purpose | Maturity |
|---|---|---|---|
| Jarvis + ARDA Core | [`apps/jarvis`](apps/jarvis/) | Desktop voice assistant, Linux/Raspberry Pi daemon, bounded agent runtime | Desktop app is the historical base; ARDA Core/Hermes features are experimental unless a specific acceptance record says otherwise |
| Shared Memory | [`services/memory`](services/memory/) | Optional local memory service, MCP endpoint, dashboard and maintenance jobs | Separate service; install only when needed; see its [component guide](services/memory/README.md) |

The memory service owns Qdrant/Ollama integration behind its API. Jarvis can also use the same store through its optional memory adapter. The service does not share a Python environment with Jarvis.

## Quick start

### macOS: Jarvis

```bash
cd apps/jarvis
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.local.example.yaml config.local.yaml
make test
```

Fill in local paths and any provider credentials in `apps/jarvis/config.local.yaml`. The file is ignored by Git. See the [Jarvis README](apps/jarvis/README.md) for microphone permissions and app usage.

### Raspberry Pi/Linux: Shared Memory and Jarvis

Install each service explicitly; each creates and manages its own environment:

```bash
cd services/memory
./scripts/install-pi.sh

cd ../../apps/jarvis
python3 -m venv .venv
.venv/bin/pip install -r requirements-rpi.txt
./scripts/install_rpi_service.sh
```

The installer may provision Docker, Qdrant, Ollama and systemd units. Review its settings first if the Pi already contains memory data or running services. No database, token, user vault, model weights, logs, or Pi snapshot is included in this repository.

## Development

From the repository root:

```bash
make test-jarvis
make test-memory
make check-shell
```

The components have separate dependency manifests because the desktop app has platform-specific speech and UI packages while the memory service has its own MCP, Qdrant, and Ollama stack.

## Security and data

- The Shared Memory HTTP service and dashboard bind to loopback by default. Use an authenticated tunnel such as Tailscale Serve for remote access; do not expose the dashboard directly to a network.
- Credentials belong in environment files or the OS secret store, never in tracked config or command output.
- Local state lives outside the source tree where possible. `.gitignore` excludes local configuration, databases, Qdrant data, tokens, logs, models, and evaluation output.
- See [SECURITY.md](SECURITY.md) and the [product layout and data policy](docs/PRODUCT_LAYOUT.md).

## Raspberry Pi inventory status

This repository was assembled from the available local Jarvis/ARDA and Shared Memory checkouts. The Raspberry Pi itself was unreachable during this pass, so its live repository list, service configuration, and existing memory owner/database mapping still need a read-only inventory before an upgrade is attempted.
