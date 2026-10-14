"""
Local, key-free mem0 configuration.

Everything runs on your machine:
  - LLM + embeddings  -> Ollama (http://localhost:11434)
  - vector store      -> native Qdrant server on :6333 (no Docker; runs as a
                         launchd agent com.mem0.qdrant, storage in
                         ~/arda-suite/services/memory/qdrant_storage). Start/stop via `mem0`.
  - history           -> SQLite at ~/.mem0/history.db (mem0 default)

No API keys. No subscription. No data leaves the computer.
"""

import os

# Silence mem0's PostHog telemetry (avoids slow network timeouts, keeps it offline).
os.environ.setdefault("MEM0_TELEMETRY", "false")

from mem0 import Memory

# nomic-embed-text outputs 768-dim vectors; the vector store dims MUST match.
EMBED_DIMS = 768

# LLM provider: "ollama" (default local) or "openai" / "nvidia" / "openrouter" / "groq"
LLM_PROVIDER = os.environ.get("MEM0_LLM_PROVIDER", "ollama").strip().lower()

# Default models per provider
if LLM_PROVIDER == "nvidia":
    DEFAULT_LLM_MODEL = "meta/llama-3.1-70b-instruct"
    DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
elif LLM_PROVIDER == "openrouter":
    DEFAULT_LLM_MODEL = "meta-llama/llama-3.3-70b-instruct"
    DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
elif LLM_PROVIDER == "groq":
    DEFAULT_LLM_MODEL = "llama-3.3-70b-versatile"
    DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
elif LLM_PROVIDER == "openai":
    DEFAULT_LLM_MODEL = "gpt-4o-mini"
    DEFAULT_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
else:
    # Ollama default:
    # qwen3:1.7b was the old default and is NOT safe here: it copied mem0's own
    # few-shot examples out of the extraction prompt and stored them as real facts
    # ("John and his dog Max...", 2026-08-06). Fact extraction needs an instruct
    # model that actually follows the prompt — qwen2.5:7b-instruct (~4.7GB, no
    # thinking mode to leak into the JSON) is the floor, not the ceiling.
    DEFAULT_LLM_MODEL = "qwen2.5:7b-instruct"
    DEFAULT_BASE_URL = os.environ.get("MEM0_OLLAMA_BASE_URL", "http://localhost:11434")

LLM_MODEL = os.environ.get("MEM0_LLM_MODEL", DEFAULT_LLM_MODEL)

# Fact-extraction discipline for a SHARED, developer-focused memory pool.
# This is a bidirectional "shared brain": the user AND many agents/tools write to
# it, so keeping junk out at write time is what stops it rotting. mem0 inserts this
# as the highest-priority "Custom Instructions" section of its extraction prompt.
CUSTOM_INSTRUCTIONS = """
This memory is a SHARED, long-lived knowledge pool for a software developer,
written to by the user and by AI coding agents. Only extract facts that will
STILL BE USEFUL WEEKS OR MONTHS FROM NOW. Be strict — when unsure, do not store.

ONLY use the input message as evidence. Never invent, guess, generalise, or fill
in plausible-sounding details. If the input does not literally state something,
it does not get stored. NEVER treat wording from these instructions as a fact
about the user — this section is a rulebook, not conversation content.

STORE (durable, reusable knowledge):
- Stable preferences: tools, languages, frameworks, coding style, workflow, and
  communication preferences.
- Lasting decisions & conventions: chosen tech stack, architecture choices,
  naming/formatting rules, adopted patterns, standing project rules.
- Reusable procedures/workflows: repeatable how-to steps.
- Hard-won gotchas & fixes: non-obvious bugs, root causes, and their resolutions.
- Durable environment facts: where things live, service/port/config locations,
  key project facts, important URLs and resources.

DO NOT STORE (noise / ephemeral):
- Play-by-play activity ("user opened a file", "ran the tests today", "is now
  debugging X") or transient task/session state.
- One-off questions, greetings, acknowledgements, small talk, or chit-chat.
- Anything trivially derivable from code, git history, or already-known facts.
- Speculative, unconfirmed, or momentary details that will not matter later.
- SECRETS: never store passwords, API keys, tokens, or private credentials —
  at most record WHERE a secret lives (e.g. "API key in .env"), never its value.

Write each fact as a concise, self-contained, timeless statement. Convert relative
dates ("today", "yesterday") to absolute ones. Preserve the user's language.
"""


def get_config() -> dict:
    """Build and return the current active mem0 configuration."""
    provider = "openai" if LLM_PROVIDER in ("openai", "nvidia", "openrouter", "groq") else "ollama"

    if provider == "openai":
        provider_key_map = {
            "nvidia": "NVIDIA_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "groq": "GROQ_API_KEY",
            "openai": "OPENAI_API_KEY",
        }
        specific_env = provider_key_map.get(LLM_PROVIDER, "OPENAI_API_KEY")
        api_key = (
            os.environ.get("MEM0_LLM_API_KEY")
            or os.environ.get(specific_env)
            or os.environ.get("OPENAI_API_KEY")
            or ""
        )
        base_url = os.environ.get("MEM0_LLM_BASE_URL", DEFAULT_BASE_URL)
        llm_config = {
            "model": LLM_MODEL,
            "temperature": float(os.environ.get("MEM0_LLM_TEMPERATURE", "0.1")),
            "max_tokens": int(os.environ.get("MEM0_LLM_MAX_TOKENS", "2000")),
            "openai_base_url": base_url,
            "api_key": api_key,
        }
    else:
        llm_config = {
            "model": LLM_MODEL,
            "temperature": float(os.environ.get("MEM0_LLM_TEMPERATURE", "0.1")),
            "max_tokens": int(os.environ.get("MEM0_LLM_MAX_TOKENS", "2000")),
            "ollama_base_url": os.environ.get("MEM0_OLLAMA_BASE_URL", "http://localhost:11434"),
        }

    return {
        "custom_instructions": CUSTOM_INSTRUCTIONS,
        "llm": {
            "provider": provider,
            "config": llm_config,
        },
        "embedder": {
            "provider": "ollama",
            "config": {
                "model": "nomic-embed-text",
                "embedding_dims": EMBED_DIMS,
                "ollama_base_url": os.environ.get("MEM0_OLLAMA_BASE_URL", "http://localhost:11434"),
            },
        },
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "collection_name": "mem0_local",
                "embedding_model_dims": EMBED_DIMS,
                "host": os.environ.get("MEM0_QDRANT_HOST", "localhost"),
                "port": int(os.environ.get("MEM0_QDRANT_PORT", "6333")),
            },
        },
    }


CONFIG = get_config()


def get_memory() -> Memory:
    """Return a ready-to-use Memory instance."""
    return Memory.from_config(get_config())

