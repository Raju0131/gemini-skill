import json
import ntpath
import os
import posixpath
import shutil
import unittest

from helpers import SCRIPTS, git, make_repo, run, write

import path_guard as pg

GUARD = os.path.join(SCRIPTS, "path_guard.py")


def verdict(pm, raw, home, workspace, loc, role="delete", base=None):
    abs_path = pm.normpath(raw if pm.isabs(raw) else pm.join(base or workspace, raw))
    return pg.worst([lv for lv, _ in pg.static_findings(pm, raw, abs_path, home, workspace, loc, role)])


class WindowsRulesTest(unittest.TestCase):
    """The drive-wipe incident happened on Windows, so test those rules on any OS via ntpath."""

    home = "C:\\Users\\Rafi"
    workspace = "D:\\Projects\\photo-app"
    env = {"SystemDrive": "C:", "SystemRoot": "C:\\Windows", "ProgramFiles": "C:\\Program Files",
           "ProgramData": "C:\\ProgramData", "TEMP": "C:\\Users\\Rafi\\AppData\\Local\\Temp"}

    def setUp(self):
        self.loc = pg.protected_locations(ntpath, self.home, self.env)

    def v(self, raw, role="delete"):
        return verdict(ntpath, raw, self.home, self.workspace, self.loc, role)

    def test_drive_roots_blocked(self):
        for raw in ("D:\\", "d:", "d:/", "C:\\", "\\\\server\\share\\"):
            self.assertEqual(self.v(raw), "BLOCKED", raw)

    def test_incident_cache_folder_is_fine_but_its_drive_is_not(self):
        self.assertEqual(self.v("D:\\Projects\\photo-app\\cache"), "OK")
        self.assertEqual(self.v("D:\\"), "BLOCKED")

    def test_workspace_and_parents_blocked(self):
        self.assertEqual(self.v("D:\\Projects\\photo-app"), "BLOCKED")
        self.assertEqual(self.v("D:\\Projects"), "BLOCKED")

    def test_system_home_and_personal_folders(self):
        self.assertEqual(self.v("C:\\Windows\\System32"), "BLOCKED")
        self.assertEqual(self.v("C:\\Program Files\\App"), "BLOCKED")
        self.assertEqual(self.v("C:\\Users"), "BLOCKED")
        self.assertEqual(self.v("C:\\Users\\Rafi"), "BLOCKED")
        self.assertEqual(self.v("C:\\Users\\Rafi\\Documents"), "BLOCKED")
        self.assertEqual(self.v("C:\\Users\\Rafi\\.ssh\\id_rsa"), "BLOCKED")

    def test_outside_workspace_and_agent_config_ask(self):
        self.assertEqual(self.v("C:\\Users\\Rafi\\Documents\\old.docx"), "ASK_USER")
        self.assertEqual(self.v("E:\\Backup\\x"), "ASK_USER")
        self.assertEqual(self.v("C:\\Users\\Rafi\\.gemini\\config\\hooks.json", "write"), "ASK_USER")
        self.assertEqual(self.v("D:\\Projects\\photo-app\\.agents\\rules\\x.md", "write"), "ASK_USER")

    def test_temp_is_ok(self):
        self.assertEqual(self.v("C:\\Users\\Rafi\\AppData\\Local\\Temp\\x"), "OK")

    def test_move_destination_rules(self):
        self.assertEqual(self.v(self.workspace, "move_destination"), "OK")
        self.assertEqual(self.v("C:\\Users\\Rafi\\Documents", "move_destination"), "ASK_USER")
        self.assertEqual(self.v("D:\\", "move_destination"), "BLOCKED")


class PosixRulesTest(unittest.TestCase):
    home = "/home/rafi"
    workspace = "/home/rafi/code/app"

    def setUp(self):
        self.loc = pg.protected_locations(posixpath, self.home, {})

    def v(self, raw, role="delete"):
        return verdict(posixpath, raw, self.home, self.workspace, self.loc, role)

    def test_roots_home_system(self):
        for raw in ("/", "/home", "/home/rafi", "~".replace("~", "/home/rafi"), "/etc/hosts", "/usr/bin",
                    "/home/rafi/Documents", "/home/rafi/.ssh"):
            self.assertEqual(self.v(raw), "BLOCKED", raw)

    def test_workspace_rules(self):
        self.assertEqual(self.v("/home/rafi/code/app"), "BLOCKED")
        self.assertEqual(self.v("/home/rafi/code"), "BLOCKED")
        self.assertEqual(self.v("build"), "OK")
        self.assertEqual(self.v("/home/rafi/other/file.txt"), "ASK_USER")
        self.assertEqual(self.v("/tmp/scratch/x"), "OK")

    def test_workspace_inside_system_folder_is_allowed(self):
        loc = pg.protected_locations(posixpath, self.home, {})
        abs_path = "/var/www/site/cache"
        findings = pg.static_findings(posixpath, abs_path, abs_path, self.home, "/var/www/site", loc)
        self.assertEqual(pg.worst([lv for lv, _ in findings]), "OK")

    def test_text_checks(self):
        self.assertEqual(self.v(""), "BLOCKED")
        self.assertEqual(self.v("a\nb"), "BLOCKED")
        self.assertEqual(self.v("*.log"), "ASK_USER")
        self.assertEqual(self.v(" build"), "ASK_USER")


class CliTest(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo()
        write(os.path.join(self.repo, "src", "a.py"), "print(1)\n")
        write(os.path.join(self.repo, "docs", "keep.md"), "doc\n")
        git(["add", "."], self.repo)
        git(["commit", "-qm", "init"], self.repo)
        write(os.path.join(self.repo, "notes.txt"), "mine\n")
        write(os.path.join(self.repo, "node_modules", "x", "i.js"), "x\n")

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def guard(self, *args):
        code, out, _ = run([GUARD, "--workspace", self.repo, "--json"] + list(args), cwd=self.repo)
        return code, json.loads(out)

    def test_committed_file_is_recoverable(self):
        code, data = self.guard("delete", "docs/keep.md")
        self.assertEqual((code, data["verdict"]), (0, "OK"))

    def test_modified_file_asks(self):
        write(os.path.join(self.repo, "src", "a.py"), "print(2)\n")
        code, data = self.guard("delete", "src/a.py")
        self.assertEqual((code, data["verdict"]), (1, "ASK_USER"))

    def test_untracked_file_asks_and_regenerable_is_ok(self):
        self.assertEqual(self.guard("delete", "notes.txt")[1]["verdict"], "ASK_USER")
        self.assertEqual(self.guard("delete", "node_modules")[1]["verdict"], "OK")

    def test_missing_and_blocked(self):
        code, data = self.guard("delete", "missing.txt")
        self.assertEqual((code, data["verdict"]), (1, "NOT_FOUND"))
        code, data = self.guard("delete", ".")
        self.assertEqual((code, data["verdict"]), (2, "BLOCKED"))

    def test_move_into_missing_folder_blocked(self):
        code, data = self.guard("move", "notes.txt", "docs/keep.md", "archive")
        self.assertEqual((code, data["verdict"]), (2, "BLOCKED"))
        os.makedirs(os.path.join(self.repo, "archive"))
        self.assertEqual(self.guard("move", "notes.txt", "docs/keep.md", "archive")[1]["verdict"], "OK")

    def test_move_collision_and_overwrite(self):
        write(os.path.join(self.repo, "other", "notes.txt"), "second\n")
        os.makedirs(os.path.join(self.repo, "archive"))
        self.assertEqual(self.guard("move", "notes.txt", "other/notes.txt", "archive")[1]["verdict"], "BLOCKED")
        write(os.path.join(self.repo, "archive", "notes.txt"), "old\n")
        self.assertEqual(self.guard("move", "notes.txt", "archive")[1]["verdict"], "ASK_USER")

    def test_write_rules(self):
        self.assertEqual(self.guard("write", "new.txt")[1]["verdict"], "OK")
        self.assertEqual(self.guard("write", "docs/keep.md")[1]["verdict"], "OK")
        self.assertEqual(self.guard("write", "notes.txt")[1]["verdict"], "ASK_USER")
        self.assertEqual(self.guard("write", "src")[1]["verdict"], "BLOCKED")

    def test_text_output_ends_with_verdict(self):
        code, out, _ = run([GUARD, "--workspace", self.repo, "delete", "docs/keep.md"], cwd=self.repo)
        self.assertEqual(out.strip().splitlines()[-1], "VERDICT: OK")

    def test_usage_errors(self):
        self.assertEqual(run([GUARD, "move", "only-one"], cwd=self.repo)[0], 3)
        self.assertEqual(run([GUARD, "explode", "x"], cwd=self.repo)[0], 3)
        self.assertEqual(run([GUARD, "--help"], cwd=self.repo)[0], 0)


if __name__ == "__main__":
    unittest.main()
