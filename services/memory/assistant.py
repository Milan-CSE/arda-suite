#!/usr/bin/env python3
"""
Interactive Central Brain Assistant (Ask Your Brain) for mem0-local.
Grounds responses in:
  1. Hybrid Sparse-Dense + Multi-Hop Knowledge Graph Memories
  2. Enforced Architectural Rules & Conventions
  3. Configured LLM (NVIDIA NIM, OpenRouter, Groq, OpenAI, or local Ollama)

Can be run interactively in the terminal, via CLI query, or consumed by the Dashboard API.
"""

import argparse
import datetime
import json
import os
import sys
import urllib.request
from typing import Any, Dict, List, Optional

MEM0_ROOT = os.path.dirname(os.path.abspath(__file__))
if MEM0_ROOT not in sys.path:
    sys.path.insert(0, MEM0_ROOT)

import config
import rules as rules_store


def recall_context(query: str, scope: str = "personal", top_k: int = 5) -> Dict[str, Any]:
    """Retrieve relevant memories and active rules for the given user query."""
    memories = []
    try:
        from mcp_server import _mem, _scoped_filters, _merge_hits
        scopes = [scope]
        if scope != "personal":
            scopes.append("personal")

        batches = [
            _mem().search(query, filters=_scoped_filters(s, None), top_k=top_k * 2).get("results", [])
            for s in scopes
        ]
        hits = _merge_hits(batches, top_k=top_k, include_superseded=False, query=query)
        memories = [
            {
                "id": str(h.get("id") or ""),
                "memory": h.get("memory") or "",
                "scope": h.get("run_id") or "personal",
                "score": h.get("score") or 0.0,
                "metadata": h.get("metadata") or {},
            }
            for h in hits
        ]
    except Exception as ex:
        # Fallback to direct scroll search if mem0 search fails
        pass

    # Retrieve active rules
    rules = rules_store.list_rules(scope=scope)

    return {
        "memories": memories,
        "rules": rules,
    }


def query_llm(system_prompt: str, user_message: str, history: Optional[List[Dict[str, str]]] = None) -> str:
    """Send prompt to the configured LLM provider."""
    active_cfg = config.get_config()
    llm_info = active_cfg.get("llm", {})
    provider = llm_info.get("provider", "ollama")
    cfg = llm_info.get("config", {})

    messages = [{"role": "system", "content": system_prompt}]
    if history:
        for turn in history:
            messages.append({"role": turn.get("role", "user"), "content": turn.get("content", "")})
    messages.append({"role": "user", "content": user_message})

    if provider == "openai":
        from openai import OpenAI
        client = OpenAI(api_key=cfg.get("api_key") or "none", base_url=cfg.get("openai_base_url"))
        resp = client.chat.completions.create(
            model=cfg.get("model", config.LLM_MODEL),
            messages=messages,
            temperature=0.2,
            max_tokens=1000,
            timeout=25,
        )
        return (resp.choices[0].message.content or "").strip()
    else:
        ollama_base = cfg.get("ollama_base_url", "http://localhost:11434")
        ollama_url = f"{ollama_base.rstrip('/')}/api/chat"
        req_body = {
            "model": cfg.get("model", config.LLM_MODEL),
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": 1000},
        }
        req = urllib.request.Request(
            ollama_url,
            data=json.dumps(req_body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.loads(r.read().decode("utf-8"))
            return data.get("message", {}).get("content", "").strip()


def ask_brain(
    question: str,
    scope: str = "personal",
    history: Optional[List[Dict[str, str]]] = None,
    top_k: int = 5,
) -> Dict[str, Any]:
    """Ask the Central Brain a question with grounded retrieval and rules."""
    ctx = recall_context(query=question, scope=scope, top_k=top_k)
    memories = ctx["memories"]
    rules = ctx["rules"]

    mem_lines = []
    for m in memories:
        graph_tag = " [Graph Linked]" if (m.get("metadata") or {}).get("graph_expanded") else ""
        mem_lines.append(f"- ({m['scope']}) {m['memory']}{graph_tag}")
    mem_block = "\n".join(mem_lines) if mem_lines else "None recorded yet."

    rule_lines = [f"- [{r.get('id')}]: {r.get('rule')}" for r in rules]
    rules_block = "\n".join(rule_lines) if rule_lines else "No specific rules configured."

    system_prompt = f"""You are the developer's Central Brain and AI Coding Assistant.
You have persistent, long-term memory of their projects, chosen conventions, tech stacks, and hard-won gotchas.

Here is the durable context recalled from the Central Brain for project '{scope}':

RECALLED FACTS & PAST DECISIONS:
{mem_block}

ENFORCED ARCHITECTURAL RULES:
{rules_block}

Instructions:
- Answer the user's question accurately, directly, and concisely.
- Prioritize their established decisions, tools, and rules over generic advice.
- If relevant past gotchas or rules apply, point them out naturally.
- Maintain the user's technical preferences and language (Turkish or English as asked).
"""

    try:
        answer = query_llm(system_prompt, question, history=history)
    except Exception as ex:
        answer = f"⚠️ Could not query configured LLM ({ex}).\n\nRecalled Context:\n{mem_block}"

    return {
        "question": question,
        "answer": answer,
        "scope": scope,
        "memories_used": memories,
        "rules_applied": rules,
        "timestamp": datetime.datetime.now().isoformat(),
    }


def interactive_chat(scope: str = "personal"):
    """Start an interactive chat session in the terminal with the Central Brain."""
    print(f"\n🧠 Central Brain Interactive Assistant (Scope: '{scope}')")
    print("Type your question or 'exit' / 'quit' to end.\n" + "═" * 60)

    history = []
    while True:
        try:
            user_input = input("\n👤 Sen: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit", "q"):
                print("Görüşmek üzere!")
                break

            print("🧠 Düşünüyor...", end="\r", flush=True)
            res = ask_brain(user_input, scope=scope, history=history)
            answer = res["answer"]
            print(f"\n🤖 Asistan:\n{answer}\n")

            if res["memories_used"]:
                print("📌 Hatırlananlar:")
                for m in res["memories_used"][:3]:
                    print(f"   • {m['memory'][:80]}")

            history.append({"role": "user", "content": user_input})
            history.append({"role": "assistant", "content": answer})
            # Keep history bounded
            if len(history) > 10:
                history = history[-10:]

        except (KeyboardInterrupt, EOFError):
            print("\nOturum kapatıldı.")
            break


def main() -> int:
    parser = argparse.ArgumentParser(description="Central Brain Interactive Assistant")
    parser.add_argument("query", nargs="?", default="", help="one-off question to ask")
    parser.add_argument("--scope", default="personal", help="project scope (default: personal)")
    parser.add_argument("--top-k", type=int, default=5, help="number of memories to recall")
    parser.add_argument("--json", action="store_true", help="output result as JSON")

    args = parser.parse_args()

    if not args.query:
        interactive_chat(scope=args.scope)
        return 0

    res = ask_brain(args.query, scope=args.scope, top_k=args.top_k)
    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        print(f"\n🤖 Asistan:\n{res['answer']}\n")
        if res["memories_used"]:
            print("📌 Hatırlanan Bilgiler:")
            for m in res["memories_used"]:
                print(f"   • ({m['scope']}) {m['memory']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
