"""Unit tests for skills_engine.py."""

import tempfile
import unittest
from pathlib import Path
import skills_engine as se


class TestSkillsEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse_frontmatter(self):
        content = """---
name: test-skill
description: "A test skill for unit testing"
triggers:
  - refactor
  - database
---

# Instructions
1. Step one
2. Step two
"""
        meta, body = se.parse_frontmatter(content)
        self.assertEqual(meta["name"], "test-skill")
        self.assertEqual(meta["description"], "A test skill for unit testing")
        self.assertEqual(meta["triggers"], ["refactor", "database"])
        self.assertIn("# Instructions", body)

    def test_create_and_load_skill(self):
        skill_file = se.create_skill(
            name="git-sandbox",
            description="Worktree sandbox for testing",
            instructions="Use git worktree add to test safely.",
            target_dir=self.base,
        )
        self.assertTrue(Path(skill_file).is_file())

        skills = se.discover_skills(search_dirs=[self.base])
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0]["name"], "git-sandbox")
        self.assertEqual(skills[0]["description"], "Worktree sandbox for testing")

    def test_match_skills_for_task(self):
        se.create_skill(
            name="db-guard",
            description="Database migration safety check",
            instructions="Always verify SQL migrations.",
            target_dir=self.base,
        )
        skills = se.discover_skills(search_dirs=[self.base])

        # Match by prompt keyword
        matched = se.match_skills_for_task("Please check the database migration", skills=skills)
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["name"], "db-guard")

        # No match for unrelated prompt
        unmatched = se.match_skills_for_task("Write a poem about nature", skills=skills)
        self.assertEqual(len(unmatched), 0)

    def test_render_skills_prompt(self):
        skills = [
            {
                "name": "db-guard",
                "description": "Database migration safety check",
                "instructions": "Step 1: Check schema.\nStep 2: Run dry-run.",
                "scripts": ["check_sql.py"],
            }
        ]
        rendered = se.render_skills_prompt(skills)
        self.assertIn("## Active Skills & Procedural Guidance", rendered)
        self.assertIn("### Skill: db-guard", rendered)
        self.assertIn("Step 1: Check schema", rendered)
        self.assertIn("check_sql.py", rendered)


if __name__ == "__main__":
    unittest.main()
