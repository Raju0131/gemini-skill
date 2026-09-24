import json
import os
import shutil
import subprocess
import tempfile
import unittest

from helpers import INSTALL, run, write

USER_HOOKS = {"my-linter": {"PostToolUse": [{"matcher": "run_command", "hooks": [{"command": "./lint.sh"}]}]}}


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="honest-flash-home-")
        self.config = os.path.join(self.home, ".gemini", "config")
        self.hooks_json = os.path.join(self.config, "hooks.json")

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def install(self, *args):
        code, out, err = run([INSTALL, "--home", self.home] + list(args))
        self.assertEqual(code, 0, out + err)
        return out

    def listing(self):
        found = []
        for root, _dirs, files in os.walk(self.home):
            if "backups" in root:
                continue
            found += [os.path.relpath(os.path.join(root, f), self.home) for f in files]
        return sorted(found)

    def test_default_install(self):
        self.install()
        self.assertTrue(os.path.isfile(os.path.join(self.config, "skills", "honest-flash", "SKILL.md")))
        self.assertTrue(os.path.isfile(os.path.join(self.config, "skills", "honest-flash", "scripts", "path_guard.py")))
        self.assertTrue(os.path.isfile(os.path.join(self.config, "rules", "honest-flash-core.md")))
        self.assertFalse(os.path.exists(self.hooks_json))

    def test_dry_run_changes_nothing(self):
        self.install("--dry-run", "--with-hooks", "--with-verifier", "--cli")
        self.assertEqual(self.listing(), [])

    def test_hooks_merge_run_and_uninstall(self):
        write(self.hooks_json, json.dumps(USER_HOOKS))
        self.install("--with-hooks", "--with-verifier", "--cli")
        with open(self.hooks_json) as fh:
            data = json.load(fh)
        self.assertEqual(data["my-linter"], USER_HOOKS["my-linter"])
        for key in ("honest-flash-anchor", "honest-flash-recorder", "honest-flash-completion-gate"):
            self.assertIn(key, data)

        # Run the installed command through a shell, the way Antigravity runs hooks.
        command = data["honest-flash-anchor"]["PreInvocation"][0]["command"]
        state = tempfile.mkdtemp(prefix="honest-flash-state-")
        self.addCleanup(shutil.rmtree, state, True)
        env = dict(os.environ, HONEST_FLASH_STATE_DIR=state)
        proc = subprocess.run(command, shell=True, input=b'{"invocationNum": 0, "conversationId": "c"}',
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Current date and time", proc.stdout.decode())

        # The user's on/off choice survives a reinstall.
        data["honest-flash-completion-gate"]["enabled"] = False
        write(self.hooks_json, json.dumps(data))
        self.install("--with-hooks")
        with open(self.hooks_json) as fh:
            self.assertFalse(json.load(fh)["honest-flash-completion-gate"]["enabled"])

        self.install("--uninstall")
        with open(self.hooks_json) as fh:
            self.assertEqual(json.load(fh), USER_HOOKS)
        self.assertEqual(self.listing(), [os.path.join(".gemini", "config", "hooks.json")])

    def test_invalid_hooks_json_is_left_alone(self):
        write(self.hooks_json, "{ not json")
        out = self.install("--with-hooks")
        self.assertIn("NOT installed", out)
        with open(self.hooks_json) as fh:
            self.assertEqual(fh.read(), "{ not json")

    def test_uninstall_keeps_foreign_files(self):
        write(os.path.join(self.config, "rules", "honest-flash-core.md"), "my own rule\n")
        self.install("--uninstall")
        self.assertTrue(os.path.exists(os.path.join(self.config, "rules", "honest-flash-core.md")))

    def test_workspace_install(self):
        ws = os.path.join(self.home, "project")
        os.makedirs(ws)
        self.install("--workspace", ws, "--with-verifier")
        for rel in (".agents/skills/honest-flash/SKILL.md", ".agents/rules/honest-flash-core.md",
                    ".agents/agents/claim-verifier.md"):
            self.assertTrue(os.path.isfile(os.path.join(ws, rel)), rel)
        self.install("--workspace", ws, "--uninstall")
        self.assertFalse(os.path.exists(os.path.join(ws, ".agents", "skills", "honest-flash")))


if __name__ == "__main__":
    unittest.main()
