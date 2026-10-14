# Shared Memory

Shared Memory is an optional local-first service for persistent memory. It
combines a Python API/MCP server, Qdrant vector storage, Ollama model support,
a dashboard, and maintenance tools. Jarvis can connect through its optional
memory adapter; the two components keep separate Python environments.

## Quick start

For a Raspberry Pi or Linux host, start with the [Pi setup guide](docs/RASPBERRY-PI-SETUP.md).
For development, see [installation](docs/INSTALL.md) and run the service's
tests from this directory with `python3 -m pytest -q`.

The installer uses local Ollama models by default: `qwen2.5:7b-instruct` for
LLM extraction and `nomic-embed-text` for embeddings. The Qwen model needs
about 4.7 GB for its weights; available memory and performance depend on the
host. Existing `~/.mem0/config.env` files are preserved.

## Data and network boundaries

- The installer binds MCP, dashboard, and Qdrant to loopback by default.
- The dashboard has no authentication. Do not expose it directly to a network.
- Memory data and credentials live outside this repository under `~/.mem0`
  and the configured Qdrant storage directory. Back them up separately.
- Ollama is the default LLM provider. If you explicitly configure a hosted
  provider, memory extraction content is sent to that provider. Keep its key in
  the ignored, permission-restricted `~/.mem0/config.env`; never commit keys.
- Use an authenticated SSH or Tailscale tunnel for remote MCP access. See
  [SECURITY.md](../../SECURITY.md) for reporting and security guidance.

## Scope

This service is independently deployable and optional. A successful local test
run does not establish Raspberry Pi performance or production readiness. Check
the host's service status and backup before upgrading an existing installation.
