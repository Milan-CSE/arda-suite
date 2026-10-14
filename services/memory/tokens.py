#!/usr/bin/env python
"""
Token management for multi-tenant / multi-client Shared Brain access.
Stores cryptographically secure tokens in ~/.mem0/tokens.json with 0600 permissions.
"""

import hmac
import json
import os
import secrets
import stat
import tempfile
import time

DEFAULT_TOKENS_FILE = os.path.expanduser("~/.mem0/tokens.json")
DEFAULT_TOKEN_FILE = os.path.expanduser("~/.mem0/mcp-token")
MIN_TOKEN_LEN = 32


def _reject_loose_permissions(path):
    if not os.path.exists(path):
        return
    mode = os.stat(path).st_mode
    if mode & (stat.S_IRGRP | stat.S_IWGRP | stat.S_IROTH | stat.S_IWOTH):
        raise PermissionError(
            f"token file {path} is group/world accessible; run: chmod 600 {path}"
        )


def _load_all_tokens(tokens_path=None) -> dict:
    path = tokens_path or os.environ.get("MEM0_MCP_TOKENS_FILE") or DEFAULT_TOKENS_FILE
    if not os.path.exists(path):
        return {}
    _reject_loose_permissions(path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {
                tok: (meta if isinstance(meta, dict) else {"user_id": str(meta)})
                for tok, meta in data.items()
                if len(tok) >= MIN_TOKEN_LEN
            }
    except Exception:
        pass
    return {}


def _save_all_tokens(tokens: dict, tokens_path=None):
    path = tokens_path or os.environ.get("MEM0_MCP_TOKENS_FILE") or DEFAULT_TOKENS_FILE
    dir_path = os.path.dirname(os.path.abspath(path))
    os.makedirs(dir_path, mode=0o700, exist_ok=True)

    # Atomic write with 0600 mode
    fd, tmp = tempfile.mkstemp(dir=dir_path, prefix="tokens-", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(tokens, f, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def generate_token(
    user_id: str,
    role: str = "developer",
    scopes: list = None,
    client_id: str = "",
    token: str = None,
    tokens_path=None,
) -> tuple[str, dict]:
    tok = token or secrets.token_hex(32)
    meta = {
        "user_id": user_id,
        "role": role,
        "scopes": scopes or [],
        "client_id": client_id or "mcp-client",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    tokens = _load_all_tokens(tokens_path)
    tokens[tok] = meta
    _save_all_tokens(tokens, tokens_path)
    return tok, meta


def list_tokens(tokens_path=None) -> list[dict]:
    tokens = _load_all_tokens(tokens_path)
    out = []
    for tok, meta in tokens.items():
        masked = tok[:6] + "..." + tok[-4:] if len(tok) >= 10 else "***"
        row = dict(meta)
        row["token_prefix"] = tok[:8]
        row["token_masked"] = masked
        row["full_token_len"] = len(tok)
        out.append(row)
    return sorted(out, key=lambda x: x.get("created_at") or "", reverse=True)


def revoke_token(token_prefix_or_id: str, tokens_path=None) -> bool:
    if not token_prefix_or_id:
        return False
    tokens = _load_all_tokens(tokens_path)
    matches = [
        tok
        for tok in tokens
        if tok == token_prefix_or_id or tok.startswith(token_prefix_or_id)
    ]
    if len(matches) == 1:
        del tokens[matches[0]]
        _save_all_tokens(tokens, tokens_path)
        return True
    return False


def verify_token(token_str: str, tokens_path=None) -> dict | None:
    if not token_str or len(token_str) < MIN_TOKEN_LEN:
        return None
    tokens = _load_all_tokens(tokens_path)
    for tok, meta in tokens.items():
        if hmac.compare_digest(tok, token_str):
            return meta
    return None
