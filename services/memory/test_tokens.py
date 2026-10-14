"""Unit tests for tokens.py (Multi-tenant token management)."""

import os
import tempfile
import unittest
from unittest.mock import patch
import tokens


class TestTokens(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tokens_file = os.path.join(self.tmpdir.name, "tokens.json")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_generate_and_list_tokens(self):
        tok1, meta1 = tokens.generate_token(
            user_id="alice",
            role="developer",
            scopes=["projA", "projB"],
            tokens_path=self.tokens_file,
        )
        self.assertEqual(len(tok1), 64)
        self.assertEqual(meta1["user_id"], "alice")
        self.assertEqual(meta1["role"], "developer")
        self.assertEqual(meta1["scopes"], ["projA", "projB"])

        tok2, meta2 = tokens.generate_token(
            user_id="bob",
            role="admin",
            tokens_path=self.tokens_file,
        )
        self.assertEqual(len(tok2), 64)
        self.assertEqual(meta2["role"], "admin")

        # Verify permissions 0600
        mode = os.stat(self.tokens_file).st_mode
        self.assertEqual(mode & 0o077, 0)

        # List tokens
        rows = tokens.list_tokens(tokens_path=self.tokens_file)
        self.assertEqual(len(rows), 2)
        users = {r["user_id"] for r in rows}
        self.assertIn("alice", users)
        self.assertIn("bob", users)

    def test_verify_token(self):
        tok, meta = tokens.generate_token(
            user_id="charlie",
            role="viewer",
            tokens_path=self.tokens_file,
        )
        # Valid token
        res = tokens.verify_token(tok, tokens_path=self.tokens_file)
        self.assertIsNotNone(res)
        self.assertEqual(res["user_id"], "charlie")
        self.assertEqual(res["role"], "viewer")

        # Invalid tokens
        self.assertIsNone(tokens.verify_token("invalid" * 5, tokens_path=self.tokens_file))
        self.assertIsNone(tokens.verify_token(tok[:-1], tokens_path=self.tokens_file))
        self.assertIsNone(tokens.verify_token("", tokens_path=self.tokens_file))

    def test_revoke_token(self):
        tok, meta = tokens.generate_token(
            user_id="dave",
            tokens_path=self.tokens_file,
        )
        prefix = tok[:8]
        # Revoke by prefix
        self.assertTrue(tokens.revoke_token(prefix, tokens_path=self.tokens_file))
        # After revocation, verify fails
        self.assertIsNone(tokens.verify_token(tok, tokens_path=self.tokens_file))
        # Revoking non-existent fails
        self.assertFalse(tokens.revoke_token("nonexistent", tokens_path=self.tokens_file))


if __name__ == "__main__":
    unittest.main()
