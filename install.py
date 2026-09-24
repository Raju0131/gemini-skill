#!/usr/bin/env python3
"""Install, update or remove Honest Flash for Google Antigravity.

Default (no options): installs globally for the Antigravity 2.0 app and the
Antigravity IDE:
  skill  -> ~/.gemini/config/skills/honest-flash/
  rule   -> ~/.gemini/config/rules/honest-flash-core.md   (always on)

Options add more:
  --with-hooks     lifecycle hooks (date anchor, loop warnings, completion gate)
                   -> ~/.gemini/config/honest-flash/honest_flash_hooks.py
                   -> entries merged into ~/.gemini/config/hooks.json
  --with-verifier  the claim-verifier subagent -> ~/.gemini/config/agents/
  --cli            also install the skill for the Antigravity CLI
                   -> ~/.gemini/antigravity-cli/skills/honest-flash/
  --workspace DIR  install skill, rule and verifier into one project
                   (DIR/.agents/...) instead of globally. Hooks stay global.
  --uninstall      remove everything this script installed (other hooks,
                   rules and skills are left alone)
  --dry-run        show what would happen without changing anything

Existing files are backed up to ~/.gemini/config/honest-flash/backups/ before
they are replaced. Restart Antigravity afterwards.
"""

import argparse
import datetime
import json
import os
import shlex
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
SKILL_NAME = "honest-flash"
SRC_SKILL = os.path.join(REPO, "honest-flash")
SRC_RULE = os.path.join(REPO, "rules", "honest-flash-core.md")
SRC_AGENT = os.path.join(REPO, "agents", "claim-verifier.md")
SRC_HOOKS = os.path.join(REPO, "hooks", "honest_flash_hooks.py")
HOOK_KEYS = ("honest-flash-anchor", "honest-flash-recorder", "honest-flash-completion-gate")
RULE_MARKER = "Honest Flash: core rules"
AGENT_MARKER = "name: claim-verifier"
SKILL_MARKER = "name: honest-flash"


class Installer(object):
    def __init__(self, home, workspace=None, dry_run=False):
        self.home = home
        self.gemini = os.path.join(home, ".gemini")
        self.config = os.path.join(self.gemini, "config")
        self.workspace = workspace
        self.dry_run = dry_run
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_root = os.path.join(self.config, "honest-flash", "backups")
        self.backup_dir = os.path.join(backup_root, stamp)
        n = 1
        while os.path.exists(self.backup_dir):
            self.backup_dir = os.path.join(backup_root, "%s-%d" % (stamp, n))
            n += 1
        base = os.path.join(workspace, ".agents") if workspace else self.config
        self.skill_dst = os.path.join(base, "skills", SKILL_NAME)
        self.rule_dst = os.path.join(base, "rules", "honest-flash-core.md")
        self.agent_dst = os.path.join(base, "agents", "claim-verifier.md")
        self.cli_skill_dst = os.path.join(self.gemini, "antigravity-cli", "skills", SKILL_NAME)
        self.hooks_script_dst = os.path.join(self.config, "honest-flash", "honest_flash_hooks.py")
        self.hooks_json = os.path.join(self.config, "hooks.json")
        self.log = []

    # -- helpers ---------------------------------------------------------
    def say(self, msg):
        self.log.append(msg)
        print(("[dry-run] " if self.dry_run else "") + msg)

    def backup(self, path):
        if not os.path.lexists(path):
            return
        rel = os.path.relpath(path, self.home) if path.startswith(self.home) else path.lstrip("/\\").replace(":", "")
        dst = os.path.join(self.backup_dir, rel)
        self.say("backup   %s -> %s" % (path, dst))
        if self.dry_run:
            return
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.copytree(path, dst)
        else:
            shutil.copy2(path, dst)

    def copy_dir(self, src, dst):
        if os.path.isdir(dst):
            self.backup(dst)
            self.say("replace  %s" % dst)
            if not self.dry_run:
                shutil.rmtree(dst)
        else:
            self.say("create   %s" % dst)
        if not self.dry_run:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    def copy_file(self, src, dst):
        if os.path.exists(dst):
            with open(src, "rb") as a, open(dst, "rb") as b:
                if a.read() == b.read():
                    self.say("same     %s" % dst)
                    return
            self.backup(dst)
            self.say("update   %s" % dst)
        else:
            self.say("create   %s" % dst)
        if not self.dry_run:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)

    # -- install ---------------------------------------------------------
    def install(self, rule=True, verifier=False, cli=False, hooks=False):
        for p in (SRC_SKILL, SRC_RULE, SRC_AGENT, SRC_HOOKS):
            if not os.path.exists(p):
                raise SystemExit("missing source file: %s (run install.py from the repository folder)" % p)
        self.copy_dir(SRC_SKILL, self.skill_dst)
        if rule:
            self.copy_file(SRC_RULE, self.rule_dst)
        if verifier:
            self.copy_file(SRC_AGENT, self.agent_dst)
        if cli:
            self.copy_dir(SRC_SKILL, self.cli_skill_dst)
        if hooks:
            self.install_hooks()

    @staticmethod
    def _python_ok(argv):
        try:
            out = subprocess.run(argv + ["-c", "import sys; print(sys.version_info >= (3, 8))"],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return out.returncode == 0 and out.stdout.strip() == b"True"

    def find_python(self):
        """Return (command string for hooks.json, argv list for testing).

        Windows: a bare launcher name from PATH ("py -3" or "python"), because a quoted
        absolute path as the first word does not run in PowerShell.
        macOS/Linux: the absolute path of a working interpreter, because desktop apps
        often start with a shorter PATH than your terminal.
        """
        if os.name == "nt":
            for name in ("py", "python", "python3"):
                exe = shutil.which(name)
                argv = ([exe, "-3"] if name == "py" else [exe]) if exe else None
                if argv and self._python_ok(argv):
                    return (name + " -3" if name == "py" else name), argv
            exe = sys.executable
            if " " in exe:
                self.say("warning  Python is not on PATH and its path contains spaces (%s). If the hooks do "
                         "not run, add Python to PATH and reinstall." % exe)
                return '"%s"' % exe, [exe]
            return exe, [exe]
        for exe in (sys.executable, shutil.which("python3"), shutil.which("python")):
            if exe and self._python_ok([exe]):
                return shlex.quote(exe), [exe]
        return "python3", [sys.executable]

    def install_hooks(self):
        existing = {}
        if os.path.exists(self.hooks_json):
            try:
                with open(self.hooks_json, "r", encoding="utf-8") as fh:
                    text = fh.read()
                existing = json.loads(text) if text.strip() else {}
            except ValueError as exc:
                self.say("ERROR    %s is not valid JSON (%s). Hooks NOT installed; fix that file first."
                         % (self.hooks_json, exc))
                return False
            if not isinstance(existing, dict):
                self.say("ERROR    %s is not a JSON object. Hooks NOT installed." % self.hooks_json)
                return False

        py_cmd, py_argv = self.find_python()
        self.copy_file(SRC_HOOKS, self.hooks_script_dst)
        script_for_test = self.hooks_script_dst if not self.dry_run else SRC_HOOKS
        test = subprocess.run(py_argv + [script_for_test, "selftest"], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=120)
        if test.returncode != 0:
            self.say("ERROR    hook selftest failed with %s; hooks.json NOT changed.\n%s"
                     % (py_cmd, test.stdout.decode("utf-8", "replace")))
            return False
        self.say("selftest passed with: %s" % py_cmd)

        script = self.hooks_script_dst.replace("\\", "/")

        def cmd(sub):
            return '%s "%s" %s' % (py_cmd, script, sub)

        entries = {
            "honest-flash-anchor": {"PreInvocation": [
                {"type": "command", "command": cmd("pre-invocation"), "timeout": 10}]},
            "honest-flash-recorder": {"PostToolUse": [
                {"matcher": "*", "hooks": [{"type": "command", "command": cmd("post-tool"), "timeout": 10}]}]},
            "honest-flash-completion-gate": {"Stop": [
                {"type": "command", "command": cmd("stop"), "timeout": 10}]},
        }
        merged = dict(existing)
        for key, value in entries.items():
            if key in merged and isinstance(merged[key], dict) and "enabled" in merged[key]:
                value = dict(value, enabled=merged[key]["enabled"])  # keep the user's on/off choice
            merged[key] = value
        if merged == existing:
            self.say("same     %s" % self.hooks_json)
            return True
        self.backup(self.hooks_json)
        self.say("%s   %s (%s)" % ("update" if existing else "create", self.hooks_json, ", ".join(HOOK_KEYS)))
        if not self.dry_run:
            os.makedirs(os.path.dirname(self.hooks_json), exist_ok=True)
            with open(self.hooks_json, "w", encoding="utf-8") as fh:
                json.dump(merged, fh, indent=2)
                fh.write("\n")
        return True

    # -- uninstall -------------------------------------------------------
    def _remove_if(self, path, marker, is_dir=False):
        check = os.path.join(path, "SKILL.md") if is_dir else path
        if not os.path.exists(check):
            return
        with open(check, "r", encoding="utf-8", errors="replace") as fh:
            if marker not in fh.read():
                self.say("skip     %s (not created by Honest Flash)" % path)
                return
        self.backup(path)
        self.say("remove   %s" % path)
        if not self.dry_run:
            if is_dir:
                shutil.rmtree(path)
            else:
                os.remove(path)

    def uninstall(self):
        self._remove_if(self.skill_dst, SKILL_MARKER, is_dir=True)
        self._remove_if(self.cli_skill_dst, SKILL_MARKER, is_dir=True)
        self._remove_if(self.rule_dst, RULE_MARKER)
        self._remove_if(self.agent_dst, AGENT_MARKER)
        if self.workspace:
            return
        if os.path.exists(self.hooks_json):
            try:
                with open(self.hooks_json, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except ValueError:
                data = None
                self.say("skip     %s (not valid JSON; remove the honest-flash-* entries by hand)" % self.hooks_json)
            if isinstance(data, dict) and any(k in data for k in HOOK_KEYS):
                self.backup(self.hooks_json)
                for k in HOOK_KEYS:
                    data.pop(k, None)
                self.say("update   %s (removed %s)" % (self.hooks_json, ", ".join(HOOK_KEYS)))
                if not self.dry_run:
                    with open(self.hooks_json, "w", encoding="utf-8") as fh:
                        json.dump(data, fh, indent=2)
                        fh.write("\n")
        if os.path.exists(self.hooks_script_dst):
            self.say("remove   %s" % self.hooks_script_dst)
            if not self.dry_run:
                os.remove(self.hooks_script_dst)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Install Honest Flash (skill, always-on rule, optional hooks and "
                                                 "verifier subagent) for Google Antigravity.")
    parser.add_argument("--workspace", metavar="DIR", help="install into DIR/.agents instead of globally")
    parser.add_argument("--with-hooks", action="store_true", help="also install the lifecycle hooks (global)")
    parser.add_argument("--with-verifier", action="store_true", help="also install the claim-verifier subagent")
    parser.add_argument("--cli", action="store_true", help="also install the skill for the Antigravity CLI")
    parser.add_argument("--no-rule", action="store_true", help="do not install the always-on rule")
    parser.add_argument("--uninstall", action="store_true", help="remove what this script installed")
    parser.add_argument("--dry-run", action="store_true", help="show actions without changing anything")
    parser.add_argument("--home", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    home = os.path.abspath(os.path.expanduser(args.home or "~"))
    workspace = os.path.abspath(os.path.expanduser(args.workspace)) if args.workspace else None
    if workspace and not os.path.isdir(workspace):
        parser.error("workspace folder does not exist: %s" % workspace)
    inst = Installer(home, workspace, dry_run=args.dry_run)

    if args.uninstall:
        inst.uninstall()
        print("Done. Restart Antigravity." if not args.dry_run else "Dry run finished; nothing was changed.")
        return 0

    if workspace and args.with_hooks:
        print("note: hooks are installed globally (~/.gemini/config/hooks.json), not per workspace.")
    inst.install(rule=not args.no_rule, verifier=args.with_verifier, cli=args.cli, hooks=args.with_hooks)
    if args.dry_run:
        print("Dry run finished; nothing was changed.")
        return 0
    print("")
    print("Installed. Next steps:")
    print("  1. Restart Antigravity (or start a new conversation).")
    print("  2. Check Settings > Customizations: the skill 'honest-flash' and the rule 'honest-flash-core' "
          "should be listed.")
    print("  3. Type /honest-flash in a conversation to force the skill.")
    if args.with_hooks:
        print("  4. Hooks: see Settings > Customizations > Hooks. To turn off only the completion gate, set "
              "\"enabled\": false on honest-flash-completion-gate in %s." % inst.hooks_json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
