#!/usr/bin/env python3
"""Unit tests for the Agents panel. Stdlib unittest only — pytest is not installed.

Run: .venv/bin/python -m unittest dashboard.test_agents -v
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import server  # noqa: E402


def write_agent(path, name, description=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\nbody\n",
        encoding="utf-8",
    )
    return path


class AgentRootsFixture(unittest.TestCase):
    """Points the agent scan roots and the usage log at temp dirs."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name).resolve()
        self.claude = base / ".claude"
        self.managed = self.claude / "agents"
        self.managed.mkdir(parents=True)
        self.plugins = self.claude / "plugins"
        self.plugins.mkdir()
        self.usage_log = base / "usage.jsonl"

        self._orig = (
            server.AGENT_DIRS,
            server.AGENT_PLUGIN_ROOTS,
            server.USAGE_LOG,
            server.CODEX_HOME,
        )
        server.AGENT_DIRS = [self.managed]
        server.AGENT_PLUGIN_ROOTS = [self.plugins]
        server.USAGE_LOG = self.usage_log
        # Nothing in the fixture lives under CODEX_HOME, so every path labels
        # as "claude" — keep it pointed somewhere that cannot match by prefix.
        server.CODEX_HOME = base / ".codex"

    def tearDown(self):
        (
            server.AGENT_DIRS,
            server.AGENT_PLUGIN_ROOTS,
            server.USAGE_LOG,
            server.CODEX_HOME,
        ) = self._orig
        self._tmp.cleanup()

    def write_usage(self, entries):
        self.usage_log.write_text(
            "".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8"
        )

    def by_name(self, agents):
        return {a["name"]: a for a in agents}


class TestAgentKey(unittest.TestCase):
    def test_folds_plugin_qualified_name_onto_bare_name(self):
        self.assertEqual(
            server._agent_key("codex-advisor:codex-advisor"),
            server._agent_key("codex-advisor"),
        )

    def test_preserves_case_so_built_in_Explore_is_not_a_plugin_explore(self):
        self.assertNotEqual(server._agent_key("Explore"), server._agent_key("explore"))

    def test_survives_empty_name(self):
        self.assertEqual(server._agent_key(""), "")


class TestReadAgents(AgentRootsFixture):
    def test_lists_managed_agents_with_editable_source(self):
        write_agent(self.managed / "code-reviewer.md", "code-reviewer", "reviews code")
        agents = self.by_name(server.read_agents())
        self.assertIn("code-reviewer", agents)
        self.assertEqual(agents["code-reviewer"]["source"], "claude/agents")
        self.assertTrue(agents["code-reviewer"]["path"])

    def test_includes_plugin_agents_qualified_by_plugin(self):
        write_agent(
            self.plugins / "cache" / "mkt" / "feature-dev" / "1.0.0" / "agents" / "code-explorer.md",
            "code-explorer",
            "explores code",
        )
        agents = self.by_name(server.read_agents())
        self.assertIn("feature-dev:code-explorer", agents)
        self.assertEqual(
            agents["feature-dev:code-explorer"]["source"], "claude/plugins:feature-dev"
        )

    def test_includes_agents_shipped_in_claude_agents_dir(self):
        write_agent(
            self.plugins / "cache" / "mkt" / "codex-advisor" / "1.1.2" / "claude-agents" / "codex-advisor.md",
            "codex-advisor",
        )
        self.assertIn("codex-advisor:codex-advisor", self.by_name(server.read_agents()))

    def test_deduplicates_marketplace_and_cache_copies(self):
        write_agent(
            self.plugins / "marketplaces" / "mkt" / "plugins" / "github-dev" / "agents" / "pr-creator.md",
            "pr-creator",
        )
        write_agent(
            self.plugins / "cache" / "mkt" / "github-dev" / "2.0.0" / "agents" / "pr-creator.md",
            "pr-creator",
        )
        names = [a["name"] for a in server.read_agents()]
        self.assertEqual(names.count("github-dev:pr-creator"), 1)

    def test_synthesizes_built_ins_seen_only_in_the_usage_log(self):
        self.write_usage([{"type": "agent", "name": "general-purpose"}] * 3)
        agents = self.by_name(server.read_agents())
        self.assertIn("general-purpose", agents)
        self.assertEqual(agents["general-purpose"]["source"], "built-in")
        self.assertEqual(agents["general-purpose"]["path"], "")

    def test_does_not_synthesize_a_built_in_for_an_agent_that_has_a_file(self):
        write_agent(self.managed / "code-reviewer.md", "code-reviewer")
        self.write_usage([{"type": "agent", "name": "code-reviewer"}])
        sources = [a["source"] for a in server.read_agents() if a["name"] == "code-reviewer"]
        self.assertEqual(sources, ["claude/agents"])

    def test_does_not_synthesize_a_built_in_for_a_plugin_agent(self):
        write_agent(
            self.plugins / "cache" / "mkt" / "codex-advisor" / "1.1.2" / "claude-agents" / "codex-advisor.md",
            "codex-advisor",
        )
        self.write_usage([{"type": "agent", "name": "codex-advisor"}])
        names = [a["name"] for a in server.read_agents()]
        self.assertEqual(names, ["codex-advisor:codex-advisor"])

    def test_ignores_skill_usage_when_synthesizing(self):
        self.write_usage([{"type": "skill", "name": "superpowers:brainstorming"}])
        self.assertEqual(server.read_agents(), [])

    def test_every_agent_carries_the_usage_key_for_its_badge(self):
        write_agent(
            self.plugins / "cache" / "mkt" / "codex-advisor" / "1.1.2" / "claude-agents" / "codex-advisor.md",
            "codex-advisor",
        )
        agent = server.read_agents()[0]
        self.assertEqual(agent["key"], "codex-advisor")


class TestAgentUsageAttribution(AgentRootsFixture):
    """One invocation count lands on exactly one card."""

    def test_bare_usage_goes_to_the_managed_agent_not_the_plugin_twin(self):
        write_agent(self.managed / "code-reviewer.md", "code-reviewer")
        write_agent(
            self.plugins / "cache" / "mkt" / "feature-dev" / "1.0.0" / "agents" / "code-reviewer.md",
            "code-reviewer",
        )
        self.write_usage([{"type": "agent", "name": "code-reviewer"}] * 13)
        counts = {a["name"]: a["count"] for a in server.read_agents()}
        self.assertEqual(counts["code-reviewer"], 13)
        self.assertEqual(counts["feature-dev:code-reviewer"], 0)

    def test_bare_usage_goes_to_the_sole_plugin_that_ships_the_name(self):
        write_agent(
            self.plugins / "cache" / "mkt" / "codex-advisor" / "1.1.2" / "claude-agents" / "codex-advisor.md",
            "codex-advisor",
        )
        self.write_usage([
            {"type": "agent", "name": "codex-advisor"},
            {"type": "agent", "name": "codex-advisor:codex-advisor"},
        ])
        agent = server.read_agents()[0]
        self.assertEqual((agent["name"], agent["count"]), ("codex-advisor:codex-advisor", 2))

    def test_leaves_an_ambiguous_name_unattributed(self):
        for plugin in ("alpha", "beta"):
            write_agent(
                self.plugins / "cache" / "mkt" / plugin / "1.0.0" / "agents" / "reviewer.md",
                "reviewer",
            )
        self.write_usage([{"type": "agent", "name": "reviewer"}] * 4)
        agents = server.read_agents()
        self.assertEqual([a["count"] for a in agents], [0, 0])
        self.assertNotIn("built-in", [a["source"] for a in agents])

    def test_built_in_Explore_is_not_absorbed_by_a_plugin_lowercase_explore(self):
        write_agent(
            self.plugins / "cache" / "mkt" / "claude-security" / "1.0.0" / "agents" / "explore.md",
            "explore",
        )
        self.write_usage([{"type": "agent", "name": "Explore"}])
        counts = {a["name"]: a["count"] for a in server.read_agents()}
        self.assertEqual(counts["Explore"], 1)
        self.assertEqual(counts["claude-security:explore"], 0)


class TestReadUsageAgentNormalization(AgentRootsFixture):
    def test_merges_bare_and_plugin_qualified_counts(self):
        self.write_usage([
            {"type": "agent", "name": "codex-advisor", "ts": "2026-08-01T00:00:00Z"},
            {"type": "agent", "name": "codex-advisor:codex-advisor", "ts": "2026-08-02T00:00:00Z"},
        ])
        usage = server.read_usage()
        self.assertEqual(usage["agents"], {"codex-advisor": 2})

    def test_keeps_the_bare_spelling_as_the_display_label(self):
        self.write_usage([
            {"type": "agent", "name": "codex-advisor:codex-advisor"},
            {"type": "agent", "name": "codex-advisor"},
        ])
        top = server.read_usage()["top_agents"]
        self.assertEqual([t["name"] for t in top], ["codex-advisor"])

    def test_does_not_normalize_skill_names(self):
        self.write_usage([
            {"type": "skill", "name": "superpowers:brainstorming"},
            {"type": "skill", "name": "brainstorming"},
        ])
        usage = server.read_usage()
        self.assertEqual(
            usage["skills"], {"superpowers:brainstorming": 1, "brainstorming": 1}
        )

    def test_reports_last_seen_timestamp_per_normalized_agent(self):
        self.write_usage([
            {"type": "agent", "name": "codex-advisor", "ts": "2026-08-01T00:00:00Z"},
            {"type": "agent", "name": "codex-advisor:codex-advisor", "ts": "2026-08-02T00:00:00Z"},
        ])
        top = server.read_usage()["top_agents"]
        self.assertEqual(top[0]["last"], "2026-08-02T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
