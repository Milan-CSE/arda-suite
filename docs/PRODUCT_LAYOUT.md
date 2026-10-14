# Product layout and data policy

## Repository boundaries

- `apps/jarvis/` contains the existing Jarvis application, ARDA Core modules, tests, and optional Hermes adapters. It retains its own dependencies and platform-specific installers.
- `services/memory/` contains the Shared Memory application, MCP transports, dashboard, maintenance workers, tests, and deployment templates. It retains its own dependencies and service lifecycle.
- The two components communicate through the existing memory/configuration boundary. They do not import each other's source tree or share a Python virtual environment.
- Ollama and Qdrant remain external local services. Their models, databases, memory contents, and credentials are not vendored.

## Data migration boundary

Only source files and selected technical documentation were copied from the local checkouts. Runtime databases, Qdrant storage, `.mem0` state, tokens, environment files, personal profile configuration, and generated evaluation records were excluded. The original source checkouts were not modified.

The Raspberry Pi was unavailable during assembly. Before upgrading an existing Pi, inventory its repositories and service units, then confirm the current `MEM0_OWNER`, Qdrant storage mount, auth-token file, and Jarvis `memory.owner` agree. Do not migrate or recreate the database as part of a code deployment.

## Public history

This repository starts with a clean Git history. Local configurations, credentials, and runtime state were excluded from the public source bundle.

## Local verification

Run `make check-shell`, `make test-jarvis`, and `make test-memory` from the root. Hardware, Pi services, microphone access, remote providers, and existing memory data require separate validation on their target environments.
