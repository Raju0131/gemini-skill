import json
import os
import shutil
import tempfile
import unittest

from helpers import SCRIPTS, git, make_repo, run, write

AUDIT = os.path.join(SCRIPTS, "diff_audit.py")

APP = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
TESTS = ("from app import add, sub\n\n\ndef test_add():\n    assert add(1, 2) == 3\n    assert add(0, 0) == 0\n\n\n"
         "def test_sub():\n    assert sub(3, 1) == 2\n")


class DiffAuditTest(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo()
        write(os.path.join(self.repo, "app.py"), APP)
        write(os.path.join(self.repo, "tests", "test_app.py"), TESTS)
        write(os.path.join(self.repo, "big.py"), "".join("x%d = %d\n" % (i, i) for i in range(80)))
        write(os.path.join(self.repo, "tests", "test_old.py"), "def test_old():\n    assert True\n")
        git(["add", "."], self.repo)
        git(["commit", "-qm", "init"], self.repo)

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def audit(self, *extra):
        code, out, _ = run([AUDIT, "--repo", self.repo, "--json"] + list(extra))
        data = json.loads(out)
        return code, data, {f["rule"] for f in data["findings"]}

    def test_clean_change(self):
        write(os.path.join(self.repo, "app.py"), APP + "\n\ndef mul(a, b):\n    return a * b\n")
        code, data, rules = self.audit()
        self.assertEqual(code, 0)
        self.assertTrue(data["summary"].startswith("AUDIT: CLEAN"))

    def test_placeholder_and_conflict_and_secret(self):
        write(os.path.join(self.repo, "app.py"),
              "def add(a, b):\n    # ... existing code ...\n    return a + b\n"
              "<<<<<<< HEAD\n=======\n>>>>>>> other\n"
              "AWS = 'AKIAIOSFODNN7EXAMPLQ'\n")
        code, data, rules = self.audit()
        self.assertEqual(code, 1)
        self.assertTrue({"placeholder", "conflict-marker", "secret"} <= rules, rules)
        self.assertNotIn("AKIAIOSFODNN7EXAMPLQ", json.dumps(data["findings"]))

    def test_weakened_skipped_and_deleted_tests(self):
        write(os.path.join(self.repo, "tests", "test_app.py"),
              "import pytest\nfrom app import add\n\n\n@pytest.mark.skip(reason='flaky')\ndef test_add():\n"
              "    assert add(1, 2) == 3\n")
        git(["rm", "-q", "tests/test_old.py"], self.repo)
        code, data, rules = self.audit()
        self.assertTrue({"test-skipped", "assertions-removed", "test-deleted"} <= rules, rules)

    def test_focus_suppression_debug_stub(self):
        write(os.path.join(self.repo, "tests", "app.test.js"), "it.only('works', () => { expect(1).toBe(1) })\n")
        write(os.path.join(self.repo, "app.py"),
              APP + "\n\ndef div(a, b):  # type: ignore\n    breakpoint()\n    raise NotImplementedError\n")
        code, data, rules = self.audit()
        self.assertTrue({"test-focused", "suppression", "debug-leftover", "stub"} <= rules, rules)

    def test_large_deletion_env_file_and_scope(self):
        write(os.path.join(self.repo, "big.py"), "x0 = 0\n")
        write(os.path.join(self.repo, ".env"), "API_KEY=abc\n")
        code, data, rules = self.audit("--scope", "app.py")
        self.assertTrue({"large-deletion", "sensitive-file", "out-of-scope"} <= rules, rules)

    def test_placeholder_values_are_not_secrets(self):
        write(os.path.join(self.repo, "app.py"), APP + "\npassword = 'changeme-example'\ntoken = os.environ['T']\n")
        self.assertNotIn("secret", self.audit()[2])

    def test_docs_are_not_checked_for_placeholders(self):
        write(os.path.join(self.repo, "README.md"), "The rest of the code is explained below.\n")
        self.assertNotIn("placeholder", self.audit()[2])

    def test_staged_only(self):
        write(os.path.join(self.repo, "app.py"), APP + "# ... rest of the code\n")
        code, data, rules = self.audit("--staged")
        self.assertNotIn("placeholder", rules)
        git(["add", "app.py"], self.repo)
        self.assertIn("placeholder", self.audit("--staged")[2])

    def test_not_a_repo(self):
        folder = tempfile.mkdtemp()
        try:
            code, out, _ = run([AUDIT, "--repo", folder])
            self.assertEqual(code, 2)
            self.assertIn("AUDIT: ERROR", out)
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_repo_without_commits(self):
        folder = make_repo()
        try:
            write(os.path.join(folder, "a.py"), "# code omitted for brevity\n")
            code, out, _ = run([AUDIT, "--repo", folder])
            self.assertEqual(code, 1)
            self.assertIn("placeholder", out)
        finally:
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
