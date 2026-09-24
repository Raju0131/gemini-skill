"""Checks the package against the documented Antigravity formats."""

import json
import os
import re
import unittest

from helpers import HOOKS, ROOT, SCRIPTS, SKILL, run

# Tool names documented at https://antigravity.google/docs/hooks (a wrong name can hang a subagent).
DOCUMENTED_TOOLS = {
    "view_file", "write_to_file", "replace_file_content", "multi_replace_file_content", "list_dir",
    "find_by_name", "grep_search", "search_web", "read_url_content", "run_command", "manage_task",
    "schedule", "list_permissions", "ask_permission", "invoke_subagent", "define_subagent", "send_message",
    "manage_subagents", "ask_question", "generate_image",
}
RULE_TRIGGERS = {"always_on", "model_decision", "glob", "manual"}


def frontmatter(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, "missing YAML frontmatter in %s" % path
    data = {}
    for line in m.group(1).splitlines():
        key, _, value = line.partition(":")
        data[key.strip()] = value.strip()
    return data, text[m.end():]


class SkillStructureTest(unittest.TestCase):
    def test_skill_frontmatter(self):
        meta, body = frontmatter(os.path.join(SKILL, "SKILL.md"))
        self.assertEqual(meta["name"], os.path.basename(SKILL))
        self.assertRegex(meta["name"], r"^[a-z0-9]+(-[a-z0-9]+)*$")
        self.assertLessEqual(len(meta["name"]), 64)
        self.assertTrue(20 < len(meta["description"]) <= 1024, len(meta["description"]))
        self.assertLess(len(body.split()), 2400, "SKILL.md should stay short for Flash models")

    def test_bengali_reply_rule_comes_first(self):
        _, body = frontmatter(os.path.join(SKILL, "SKILL.md"))
        first_section = body.split("## 1.")[0]
        self.assertIn("Always reply to the user in Bengali", first_section)
        self.assertIn("Banglish", first_section)
        _, rule_body = frontmatter(os.path.join(ROOT, "rules", "honest-flash-core.md"))
        self.assertIn("Always reply to the user in Bengali", rule_body.split("1. **")[0])

    def test_final_report_template_is_bengali(self):
        with open(os.path.join(SKILL, "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        for label in ("ফলাফল:", "পরিবর্তন:", "চালানো যাচাই:", "যা হয়নি / সমস্যা:", "আপনার করণীয়:"):
            self.assertIn(label, text)

    def test_links_resolve(self):
        md_files = [os.path.join(SKILL, "SKILL.md")]
        md_files += [os.path.join(SKILL, "references", f) for f in os.listdir(os.path.join(SKILL, "references"))]
        md_files += [os.path.join(ROOT, "README.md"), os.path.join(ROOT, "docs", "gemini-flash-weaknesses.md"),
                     os.path.join(ROOT, "permissions", "recommended-permissions.md")]
        for path in md_files:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            for target in re.findall(r"\]\(([^)#\s]+)\)", text):
                if re.match(r"^[a-z]+://", target):
                    continue
                resolved = os.path.normpath(os.path.join(os.path.dirname(path), target))
                self.assertTrue(os.path.exists(resolved), "%s links to missing %s" % (path, target))

    def test_scripts_mentioned_in_skill_exist(self):
        with open(os.path.join(SKILL, "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        for name in set(re.findall(r"`(?:scripts/)?([a-z_]+\.py)", text)):
            self.assertTrue(os.path.isfile(os.path.join(SCRIPTS, name)), name)

    def test_rule_frontmatter_and_size(self):
        path = os.path.join(ROOT, "rules", "honest-flash-core.md")
        meta, _ = frontmatter(path)
        self.assertIn(meta["trigger"], RULE_TRIGGERS)
        self.assertLess(os.path.getsize(path), 24000)

    def test_verifier_tools_are_documented(self):
        meta, _ = frontmatter(os.path.join(ROOT, "agents", "claim-verifier.md"))
        tools = set(json.loads(meta["tools"]))
        self.assertTrue(tools <= DOCUMENTED_TOOLS, tools - DOCUMENTED_TOOLS)
        self.assertFalse(tools & {"write_to_file", "replace_file_content", "multi_replace_file_content"})
        self.assertIn(meta["model"], {"inherit", "flash", "pro"})
        self.assertIn(meta["commandExecutionPolicy"], {"off", "auto", "eager", "sandbox"})

    def test_hooks_example_shape(self):
        with open(os.path.join(ROOT, "hooks", "hooks.example.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertIn("command", data["honest-flash-anchor"]["PreInvocation"][0])
        self.assertIn("command", data["honest-flash-recorder"]["PostToolUse"][0]["hooks"][0])
        self.assertIn("command", data["honest-flash-completion-gate"]["Stop"][0])

    def test_every_script_has_help(self):
        for script in (os.path.join(SCRIPTS, "path_guard.py"), os.path.join(SCRIPTS, "diff_audit.py"), HOOKS,
                       os.path.join(ROOT, "install.py")):
            code, out, err = run([script, "--help"])
            self.assertEqual(code, 0, script + err)
            self.assertTrue(out.strip(), script)


if __name__ == "__main__":
    unittest.main()
