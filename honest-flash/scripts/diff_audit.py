#!/usr/bin/env python3
"""Audit your git changes before you report coding work as done.

Scans the changes in a git repository (tracked edits compared with HEAD, plus
new untracked files) for problems that agents often leave behind:

  HIGH    placeholder code ("// ... existing code ...", "rest of the code"),
          merge conflict markers, skipped or focused tests, removed test
          assertions, deleted test files, secrets and credential files
  MEDIUM  new lint/type suppressions, debugger leftovers, NotImplemented
          stubs, large net deletions, deleted files, changes outside --scope
  LOW     dependency/build/CI file changes, new TODO/FIXME, console.log

Examples:
  python3 diff_audit.py
  python3 diff_audit.py --repo /path/to/project
  python3 diff_audit.py --staged
  python3 diff_audit.py --base main --scope src/ tests/
  python3 diff_audit.py --json

The last line is the result: "AUDIT: CLEAN ..." or "AUDIT: <n> high, ...".
Fix every HIGH and MEDIUM finding, or explain in your report why it is intended.
Exit codes: 0 = no HIGH/MEDIUM findings, 1 = findings, 2 = error (for example
not a git repository).
"""

import argparse
import json
import os
import re
import subprocess
import sys

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
DOC_EXTENSIONS = (".md", ".markdown", ".txt", ".rst", ".adoc", ".org")
MAX_UNTRACKED_BYTES = 1024 * 1024

TEST_PATH_RE = re.compile(
    r"(^|/)(tests?|__tests__|specs?|testing)/"
    r"|(^|/)test_[^/]*\.py$|_test\.(py|go|rb|exs?)$|_spec\.rb$"
    r"|\.(test|spec)\.[cm]?[jt]sx?$|(Test|Tests|IT)\.(java|kt|cs|scala)$", re.I)

PLACEHOLDER_RES = [re.compile(p, re.I) for p in [
    r"(\.\.\.|…)\s*\(?\s*(existing|rest of|remaining|previous|other|unchanged|same as|original|more)\b",
    r"\b(existing|previous|original|remaining|other|unchanged)\s+(code|implementation|logic|content|methods?|"
    r"functions?|imports?|styles?|properties|fields|tests?|handlers?)\b.{0,25}"
    r"(\.\.\.|…|here|unchanged|omitted|remains?|stays?|goes here|as before|as is)",
    r"\b(rest|remainder)\s+of\s+(the\s+)?(code|file|implementation|function|class|component|module|logic|"
    r"content|method|script)\b",
    r"\b(code|implementation|content|logic)\s+(omitted|truncated|elided|abbreviated)\b",
    r"\bfor\s+brevity\b",
    r"\b(your|actual|real)\s+(code|implementation|logic)\s+(goes\s+)?here\b",
    r"\bTODO\b.{0,30}\b(implement|finish|complete|fill in|add (the )?(logic|implementation))\b",
]]
CONFLICT_RE = re.compile(r"^(<{7}|>{7})(\s|$)|^={7}$")
SKIP_RE = re.compile(
    r"@pytest\.mark\.skip|\bpytest\.skip\(|@unittest\.skip|\b(it|test|describe|context|suite)\.skip\b"
    r"|\bx(it|describe|test)\s*\(|@Disabled\b|@Ignore\b|\bt\.Skip(Now|f)?\(|#\[ignore\]|\bskip\s*\(\s*\)")
FOCUS_RE = re.compile(r"\b(it|test|describe|context|suite)\.only\s*\(|\bf(it|describe)\s*\(")
ASSERT_RE = re.compile(r"\bassert|\bexpect\s*\(|\.should\b|\bt\.(Errorf?|Fatalf?)\(|\brequire\.\w+\(|\bverify\(")
SUPPRESS_RE = re.compile(
    r"#\s*type:\s*ignore|#\s*noqa|eslint-disable|@ts-ignore|@ts-nocheck|@ts-expect-error|//\s*nolint"
    r"|#\[allow\(|@SuppressWarnings|#\s*pylint:\s*disable|pragma:\s*no cover|istanbul ignore")
DEBUG_RE = re.compile(r"\bdebugger\b\s*;?\s*$|\bbreakpoint\(\)|pdb\.set_trace\(|^\s*import\s+i?pdb\b"
                      r"|binding\.pry|\bvar_dump\(")
STUB_RE = re.compile(r"raise\s+NotImplementedError|throw\s+new\s+Error\(\s*['\"`]not implemented"
                     r"|\bunimplemented!\(|\btodo!\(|NotImplementedException", re.I)
TODO_RE = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b")
CONSOLE_RE = re.compile(r"\bconsole\.(log|debug)\(")

SECRET_RES = [
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private key", re.compile(r"-----BEGIN (RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}|\bgithub_pat_[A-Za-z0-9_]{40,}")),
    ("Slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}")),
    ("API secret key", re.compile(r"\bsk-(ant-|proj-)?[A-Za-z0-9_\-]{20,}")),
    ("Stripe key", re.compile(r"\b(sk|rk)_(live|test)_[0-9A-Za-z]{16,}")),
    ("hardcoded credential", re.compile(
        r"(?i)\b(api[_-]?key|secret|token|passw(or)?d|pwd|access[_-]?key|client[_-]?secret|auth)\b[\"']?\s*[:=]"
        r"\s*[\"']([^\"'\s]{8,})[\"']")),
]
SECRET_PLACEHOLDER_RE = re.compile(r"(?i)x{4,}|your[_-]|changeme|example|dummy|placeholder|<[^>]*>|\$\{|"
                                   r"process\.env|os\.environ|getenv|redacted|\*{4,}|test|fake|sample")
SENSITIVE_FILE_RE = re.compile(
    r"(^|/)(\.env(\.[\w-]+)?|id_rsa|id_dsa|id_ecdsa|id_ed25519|credentials\.json|service[-_]account[^/]*\.json"
    r"|[^/]+\.(pem|p12|pfx|key|keystore|jks))$", re.I)
SENSITIVE_FILE_OK_RE = re.compile(r"\.env\.(example|sample|template|dist|defaults?)$", re.I)
CONFIG_FILE_RE = re.compile(
    r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|bun\.lockb?|poetry\.lock|uv\.lock|Pipfile\.lock|"
    r"Cargo\.lock|go\.sum|Gemfile\.lock|composer\.lock|package\.json|pyproject\.toml|setup\.py|setup\.cfg|"
    r"requirements[^/]*\.txt|go\.mod|Cargo\.toml|Gemfile|Dockerfile|docker-compose[^/]*\.ya?ml|Makefile|"
    r"\.github/workflows/[^/]+|\.gitlab-ci\.yml|tsconfig[^/]*\.json|\.eslintrc[^/]*|\.pre-commit-config\.yaml)$")


def git(args, cwd, check=True):
    try:
        proc = subprocess.run(["git", "-c", "core.quotepath=off"] + args, cwd=cwd,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("could not run git: %s" % exc)
    if check and proc.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), proc.stderr.decode("utf-8", "replace").strip()))
    return proc.stdout.decode("utf-8", "replace")


def unquote_path(p):
    p = p.rstrip("\n")
    if p.startswith('"') and p.endswith('"'):
        p = p[1:-1]
        p = re.sub(r'\\(["\\tn])', lambda m: {"t": "\t", "n": "\n"}.get(m.group(1), m.group(1)), p)
    return p


def strip_prefix(p):
    p = unquote_path(p)
    if p == "/dev/null":
        return None
    if p[:2] in ("a/", "b/"):
        return p[2:]
    return p


def parse_diff(text):
    """Return (added, removed) lists of (path, line_no, text)."""
    added, removed = [], []
    old_path = new_path = None
    in_hunk = False
    old_ln = new_ln = 0
    for line in text.split("\n"):
        if line.startswith("diff --git "):
            in_hunk = False
            old_path = new_path = None
            continue
        if not in_hunk:
            if line.startswith("--- "):
                old_path = strip_prefix(line[4:])
            elif line.startswith("+++ "):
                new_path = strip_prefix(line[4:])
            elif line.startswith("@@"):
                in_hunk = True
        if in_hunk and line.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if m:
                old_ln, new_ln = int(m.group(1)), int(m.group(2))
            continue
        if not in_hunk:
            continue
        if line.startswith("+"):
            added.append((new_path or old_path, new_ln, line[1:]))
            new_ln += 1
        elif line.startswith("-"):
            removed.append((old_path or new_path, old_ln, line[1:]))
            old_ln += 1
        elif line.startswith(" "):
            old_ln += 1
            new_ln += 1
    return added, removed


def read_untracked(repo, rel):
    path = os.path.join(repo, rel)
    try:
        if os.path.getsize(path) > MAX_UNTRACKED_BYTES:
            return None
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    if b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", "replace").splitlines()


def snippet(text, limit=110):
    text = text.strip()
    return text if len(text) <= limit else text[:limit - 3] + "..."


def mask(text):
    return re.sub(r"([A-Za-z0-9_\-+/=]{4})[A-Za-z0-9_\-+/=]{6,}", r"\1******", text)


def is_doc(path):
    return path.lower().endswith(DOC_EXTENSIONS)


def audit(repo, base=None, staged=False, scope=None, max_per_rule=20):
    top = git(["rev-parse", "--show-toplevel"], repo).strip()
    has_head = subprocess.run(["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=top,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode == 0
    if staged:
        ref_args, compared = ["--cached"] + ([] if has_head else [EMPTY_TREE]), "staged changes"
    else:
        ref = base or ("HEAD" if has_head else EMPTY_TREE)
        ref_args = [ref]
        compared = "compared with %s, including uncommitted and untracked changes" % (base or "HEAD")
    diff_text = git(["diff", "--no-color", "--no-ext-diff", "-U0", "-M"] + ref_args, top)
    numstat = git(["diff", "--numstat", "-M"] + ref_args, top)
    name_status = git(["diff", "--name-status", "-M"] + ref_args, top)
    added, removed = parse_diff(diff_text)

    files = {}
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            a, d, path = parts[0], parts[1], parts[-1]
            files[unquote_path(path)] = {"added": int(a) if a.isdigit() else 0,
                                         "deleted": int(d) if d.isdigit() else 0, "status": "M"}
    for line in name_status.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            status, path = parts[0][:1], unquote_path(parts[-1])
            files.setdefault(path, {"added": 0, "deleted": 0})["status"] = status

    untracked = []
    if not staged:
        for rel in git(["ls-files", "--others", "--exclude-standard", "-z"], top).split("\0"):
            if not rel:
                continue
            untracked.append(rel)
            lines = read_untracked(top, rel)
            files[rel] = {"added": len(lines or []), "deleted": 0, "status": "?"}
            for i, text in enumerate(lines or [], 1):
                added.append((rel, i, text))

    findings = []

    def add(level, rule, path, line, message):
        findings.append({"level": level, "rule": rule, "path": path, "line": line, "message": message})

    for path, ln, text in added:
        if not path:
            continue
        test = bool(TEST_PATH_RE.search(path))
        if CONFLICT_RE.search(text):
            add("HIGH", "conflict-marker", path, ln, "merge conflict marker: " + snippet(text))
        if not is_doc(path):
            for rx in PLACEHOLDER_RES:
                if rx.search(text):
                    add("HIGH", "placeholder", path, ln, "placeholder instead of real code: " + snippet(text))
                    break
            if STUB_RE.search(text):
                add("MEDIUM", "stub", path, ln, "not-implemented stub: " + snippet(text))
            if SUPPRESS_RE.search(text):
                add("MEDIUM", "suppression", path, ln, "new lint/type suppression: " + snippet(text))
            if DEBUG_RE.search(text):
                add("MEDIUM", "debug-leftover", path, ln, "debugger leftover: " + snippet(text))
            if CONSOLE_RE.search(text) and not test:
                add("LOW", "console-log", path, ln, "console output left in code: " + snippet(text))
            if TODO_RE.search(text):
                add("LOW", "todo", path, ln, "new TODO/FIXME: " + snippet(text))
        if test and SKIP_RE.search(text):
            add("HIGH", "test-skipped", path, ln, "test skipped or disabled: " + snippet(text))
        if test and FOCUS_RE.search(text):
            add("HIGH", "test-focused", path, ln, "focused test (.only/fit) silently skips all other tests: "
                + snippet(text))
        for name, rx in SECRET_RES:
            m = rx.search(text)
            if not m:
                continue
            value = m.group(m.lastindex) if name == "hardcoded credential" and m.lastindex else m.group(0)
            if name == "hardcoded credential" and SECRET_PLACEHOLDER_RE.search(value):
                continue
            add("HIGH", "secret", path, ln, "possible %s: %s" % (name, mask(snippet(text))))
            break

    # Removed vs added assertions, per test file.
    per_file = {}
    for path, ln, text in removed:
        if path and TEST_PATH_RE.search(path) and ASSERT_RE.search(text):
            per_file.setdefault(path, [0, 0, []])
            per_file[path][0] += 1
            per_file[path][2].append((ln, text))
    for path, ln, text in added:
        if path and TEST_PATH_RE.search(path) and ASSERT_RE.search(text):
            per_file.setdefault(path, [0, 0, []])
            per_file[path][1] += 1
    for path, (n_removed, n_added, examples) in per_file.items():
        if n_removed > n_added:
            ex = examples[0]
            add("HIGH", "assertions-removed", path, ex[0],
                "%d assertion line(s) removed, %d added — tests may have been weakened (e.g. %s)"
                % (n_removed, n_added, snippet(ex[1], 60)))

    for path, info in sorted(files.items()):
        status = info.get("status", "M")
        if status == "D":
            if TEST_PATH_RE.search(path):
                add("HIGH", "test-deleted", path, None, "test file deleted")
            else:
                add("MEDIUM", "file-deleted", path, None, "file deleted — confirm this was requested")
        elif info["deleted"] >= 50 and info["deleted"] > 3 * info["added"]:
            add("MEDIUM", "large-deletion", path, None,
                "large net deletion (+%d −%d) — check nothing was lost by a rewrite or truncation"
                % (info["added"], info["deleted"]))
        if status != "D" and SENSITIVE_FILE_RE.search(path) and not SENSITIVE_FILE_OK_RE.search(path):
            add("HIGH", "sensitive-file", path, None, "credential or key file added/changed — must not be committed")
        if CONFIG_FILE_RE.search(path):
            add("LOW", "config-change", path, None, "dependency/build/CI file changed — mention it in the report")
        if scope and not any(path == s.rstrip("/") or path.startswith(s.rstrip("/") + "/") for s in scope):
            add("MEDIUM", "out-of-scope", path, None, "changed outside the allowed scope (%s)" % ", ".join(scope))

    # Cap repeated findings per rule to keep output readable.
    capped, counts = [], {}
    for f in findings:
        counts[f["rule"]] = counts.get(f["rule"], 0) + 1
        if counts[f["rule"]] <= max_per_rule:
            capped.append(f)
    hidden = {r: n - max_per_rule for r, n in counts.items() if n > max_per_rule}

    totals = {lv: len([f for f in findings if f["level"] == lv]) for lv in ("HIGH", "MEDIUM", "LOW")}
    return {"repo": top, "compared": compared, "files": files, "untracked": untracked,
            "findings": capped, "hidden": hidden, "totals": totals}


def summary_line(totals):
    if totals["HIGH"] == 0 and totals["MEDIUM"] == 0:
        return "AUDIT: CLEAN" + (" (only %d low finding(s))" % totals["LOW"] if totals["LOW"] else " (no findings)")
    return ("AUDIT: %d high, %d medium, %d low — fix or explain every HIGH and MEDIUM finding in your report"
            % (totals["HIGH"], totals["MEDIUM"], totals["LOW"]))


def format_text(result):
    out = ["diff_audit — repo: %s (%s)" % (result["repo"], result["compared"])]
    files = result["files"]
    add_total = sum(f["added"] for f in files.values())
    del_total = sum(f["deleted"] for f in files.values())
    out.append("Changed files: %d (+%d −%d); new untracked files: %d"
               % (len(files), add_total, del_total, len(result["untracked"])))
    for path, info in sorted(files.items()):
        out.append("  %s %s (+%d −%d)" % (info.get("status", "M"), path, info["added"], info["deleted"]))
    for level in ("HIGH", "MEDIUM", "LOW"):
        items = [f for f in result["findings"] if f["level"] == level]
        if not items:
            continue
        out.append("%s (%d)" % (level, result["totals"][level]))
        for f in items:
            where = f["path"] + (":%d" % f["line"] if f["line"] else "")
            out.append("  %s  %s" % (where, f["message"]))
    for rule, n in sorted(result["hidden"].items()):
        out.append("  (%d more '%s' finding(s) not shown)" % (n, rule))
    out.append(summary_line(result["totals"]))
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="diff_audit.py",
        description="Scan git changes for placeholders, weakened or skipped tests, conflict markers, secrets, "
                    "debug leftovers and large deletions. Run before reporting coding work as done.",
        epilog="Exit codes: 0 no HIGH/MEDIUM findings, 1 findings, 2 error.")
    parser.add_argument("--repo", default=".", help="folder inside the git repository (default: current folder)")
    parser.add_argument("--base", help="compare the working tree with this ref instead of HEAD (e.g. main)")
    parser.add_argument("--staged", action="store_true", help="audit only staged changes")
    parser.add_argument("--scope", nargs="+", metavar="PATH",
                        help="repo-relative paths the task may touch; changes elsewhere are reported")
    parser.add_argument("--max-per-rule", type=int, default=20, help="max findings shown per rule (default 20)")
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        result = audit(args.repo, base=args.base, staged=args.staged, scope=args.scope,
                       max_per_rule=args.max_per_rule)
    except RuntimeError as exc:
        print("diff_audit: %s" % exc)
        print("AUDIT: ERROR — not run; report the audit as NOT RUN")
        return 2
    if args.json:
        result["summary"] = summary_line(result["totals"])
        print(json.dumps(result, indent=2))
    else:
        print(format_text(result))
    return 1 if result["totals"]["HIGH"] or result["totals"]["MEDIUM"] else 0


if __name__ == "__main__":
    sys.exit(main())
