#!/usr/bin/env python3
"""Honest Flash lifecycle hooks for Google Antigravity (2.0 app, IDE and CLI).

One subcommand per hook event. Antigravity sends the event as JSON on stdin and
reads a JSON answer from stdout (see https://antigravity.google/docs/hooks).

  pre-invocation  PreInvocation. Injects an ephemeral system message with the
                  real date and time from this computer, loop warnings, error
                  streak warnings and a periodic "stay on task" checkpoint.
  post-tool       PostToolUse. Records each tool call (name, hash of the
                  arguments, error flag, whether it changed state) for the
                  other two hooks. Always answers {}.
  stop            Stop. Completion gate: if the agent changed files or ran
                  state-changing commands in this turn, the first stop is
                  turned into one more step that asks for an evidence-based
                  final report. The next stop is always allowed.
  selftest        Runs the handlers on sample payloads in a temporary state
                  folder and prints PASS/FAIL.

These hooks never gate tool calls (no PreToolUse), so they cannot loosen or
tighten your permission settings. On any internal error they answer {}.

Environment variables:
  HONEST_FLASH_GATE=0          turn the completion gate off
  HONEST_FLASH_STATE_DIR=DIR   where per-conversation state is stored
                               (default: <system temp>/honest-flash-hooks)
"""

import datetime
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time

PREFIX = "[honest-flash]"
MAX_CALLS = 80
LOOP_REPEAT = 3
ERROR_STREAK = 3
CHECKPOINT_EVERY = 15
ANCHOR_REFRESH_SECONDS = 20 * 60
STATE_TTL_SECONDS = 3 * 24 * 3600

EDIT_TOOLS = {"write_to_file", "replace_file_content", "multi_replace_file_content"}
STATEFUL_TOOLS = {"invoke_subagent", "schedule"}
NEUTRAL_TOOLS = {
    "view_file", "list_dir", "find_by_name", "grep_search", "search_web", "read_url_content",
    "list_permissions", "ask_permission", "ask_question", "manage_task", "manage_subagents",
    "send_message", "generate_image", "define_subagent",
}
BROWSER_ACTION_RE = re.compile(r"click|type|fill|submit|press|select|upload|drag|input|key|execute|evaluate|script",
                               re.I)
UNKNOWN_WRITE_RE = re.compile(r"write|create|update|delete|remove|insert|send|post|publish|deploy|commit|push|"
                              r"execute|mutat|upload|drop", re.I)

READONLY_COMMANDS = {
    "ls", "dir", "pwd", "cd", "pushd", "popd", "cat", "type", "head", "tail", "less", "more", "grep", "rg",
    "egrep", "fgrep", "find", "which", "where", "whereis", "echo", "printf", "wc", "tree", "stat", "file",
    "du", "df", "date", "whoami", "hostname", "uname", "printenv", "ps", "sort", "uniq", "cut", "diff",
    "cmp", "realpath", "readlink", "basename", "dirname", "test", "true", "false", "clear", "jq",
    "sha256sum", "md5sum", "shasum", "get-childitem", "gci", "get-content", "gc", "get-location", "gl",
    "set-location", "sl", "select-string", "sls", "get-item", "gi", "get-date", "get-command", "gcm",
    "get-process", "gps", "measure-object", "measure", "test-path", "resolve-path", "write-output",
    "write-host", "select-object", "select", "format-list", "fl", "format-table", "ft", "out-string",
    "get-filehash", "where-object", "sort-object",
}
GIT_READONLY = {
    "status", "diff", "log", "show", "rev-parse", "ls-files", "describe", "blame", "grep", "shortlog",
    "reflog", "cat-file", "ls-tree", "whatchanged",
}
GIT_WRITE_FLAGS = {
    "branch": {"-d", "-D", "-m", "-M", "-c", "-C", "-f", "--delete", "--move", "--copy", "--force", "-u",
               "--set-upstream-to", "--unset-upstream", "--edit-description"},
    "tag": {"-a", "-s", "-u", "-d", "-f", "-m", "-F", "--annotate", "--sign", "--delete", "--force"},
}

MSG_ANCHOR = ("%s Current date and time on the user's computer: %s (UTC%s). Use this for anything "
              "time-sensitive. Your training data ends before this date, so verify recent facts with a tool "
              "before relying on them or disputing the user.")
MSG_CHECKPOINT = ("%s Checkpoint: re-read the user's latest request and your task list, and keep doing exactly "
                  "what was asked. Report only results you verified with tools in this session; mark the rest "
                  "UNVERIFIED or NOT RUN.")
MSG_LOOP = ("%s Loop warning: you have made the same %s call %d times since your last file edit, with nothing "
            "changed in between. Do not repeat it. Say what you learned, then try a different approach or stop "
            "and ask the user.")
MSG_ERRORS = ("%s The last %d tool calls failed. Do not retry the same way. Read the full error messages, check "
              "paths and the working directory, then change your approach or ask the user. Do not report "
              "success for steps that failed.")
MSG_GATE = ("%s Completion check before you finish. You changed files or ran state-changing commands in this "
            "turn. If you are waiting for the user's answer or plan approval, say so in one line and stop "
            "without doing more work. Otherwise make sure your final message lists: (1) each change with "
            "evidence from this turn (command and result, or a re-read), (2) every check that failed or was "
            "not run, marked FAILED or NOT RUN, (3) anything requested but not done. If the relevant tests or "
            "checks have not run since your last edit and they are quick and safe, run them now. Never claim "
            "success without evidence. If your last message already did all this, reply with one short line "
            "saying so and stop.")


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------

def state_dir():
    return os.environ.get("HONEST_FLASH_STATE_DIR") or os.path.join(tempfile.gettempdir(), "honest-flash-hooks")


def state_path(conversation_id):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", conversation_id or "unknown")[:100] or "unknown"
    return os.path.join(state_dir(), safe + ".json")


def new_state():
    return {"v": 1, "calls": [], "total": 0, "edits": 0, "warned": [], "err_warned_at": -1,
            "anchor_t": 0.0, "mut": False, "gate_pending": False, "gates": 0}


def load_state(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and data.get("v") == 1:
            base = new_state()
            base.update(data)
            return base
    except (OSError, ValueError):
        pass
    return new_state()


def save_state(path, state):
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.replace(tmp, path)


def cleanup_old_state(directory):
    marker = os.path.join(directory, ".last_cleanup")
    try:
        if time.time() - os.path.getmtime(marker) < 3600:
            return
    except OSError:
        pass
    try:
        with open(marker, "w") as fh:
            fh.write(str(time.time()))
        for name in os.listdir(directory):
            p = os.path.join(directory, name)
            if name.endswith(".json") and time.time() - os.path.getmtime(p) > STATE_TTL_SECONDS:
                os.remove(p)
    except OSError:
        pass


def with_state(conversation_id, fn):
    """Run fn(state) under a best-effort lock and save the state afterwards."""
    directory = state_dir()
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        return fn(new_state())
    cleanup_old_state(directory)
    path = state_path(conversation_id)
    lock = path + ".lock"
    fd = None
    for _ in range(40):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(lock) > 10:
                    os.remove(lock)
                    continue
            except OSError:
                pass
            time.sleep(0.025)
        except OSError:
            break
    try:
        state = load_state(path)
        result = fn(state)
        if fd is not None:
            save_state(path, state)
        return result
    finally:
        if fd is not None:
            os.close(fd)
            try:
                os.remove(lock)
            except OSError:
                pass


# --------------------------------------------------------------------------
# Classifying tool calls
# --------------------------------------------------------------------------

def _segments(command_line):
    return [s.strip() for s in re.split(r"&&|\|\||[;|\n]", command_line or "") if s.strip()]


def _first_tokens(segment):
    tokens = re.findall(r"\"[^\"]*\"|'[^']*'|\S+", segment)
    tokens = [t.strip("\"'") for t in tokens]
    while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
        tokens.pop(0)
    return tokens


def git_is_readonly(rest):
    """rest = git arguments after global options, starting with the subcommand."""
    if not rest:
        return True
    sub, args = rest[0], rest[1:]
    if sub in GIT_READONLY:
        return True
    if sub in ("branch", "tag"):
        return all(a.startswith("-") and a not in GIT_WRITE_FLAGS[sub] for a in args)
    if sub == "stash":
        return args[:1] in (["list"], ["show"])
    if sub == "remote":
        return not args or args[0] in ("-v", "--verbose", "show", "get-url")
    if sub == "worktree":
        return args[:1] == ["list"]
    if sub == "config":
        return (any(a in ("--get", "--get-all", "--get-regexp", "--list", "-l") for a in args)
                or (len(args) == 1 and not args[0].startswith("-")))
    return False


def command_is_readonly(command_line):
    segments = _segments(command_line)
    if not segments:
        return True
    for seg in segments:
        if re.search(r"(^|[^0-9&>])>{1,2}(?!&)", seg.replace("2>&1", "").replace(">&2", "")):
            return False  # output redirection writes a file
        tokens = _first_tokens(seg)
        if not tokens:
            continue
        cmd = os.path.basename(tokens[0].replace("\\", "/")).lower()
        if cmd.endswith(".exe"):
            cmd = cmd[:-4]
        if cmd == "git":
            rest = tokens[1:]
            while rest and rest[0].startswith("-"):
                opt = rest.pop(0)
                if opt in ("-C", "-c") and rest:
                    rest.pop(0)
            if not git_is_readonly(rest):
                return False
            continue
        if cmd not in READONLY_COMMANDS:
            return False
    return True


def _norm(p):
    return os.path.normcase(os.path.normpath(os.path.expanduser(p or "")))


def is_state_changing(name, args, artifact_dir=None):
    args = args if isinstance(args, dict) else {}
    if name in EDIT_TOOLS:
        if args.get("IsArtifact") in (True, "true", "True"):
            return False
        target = _norm(str(args.get("TargetFile", "")))
        if artifact_dir and target.startswith(_norm(artifact_dir)):
            return False
        if re.search(r"[\\/]\.gemini[\\/]antigravity[^\\/]*[\\/]brain[\\/]", target):
            return False
        return True
    if name == "run_command":
        return not command_is_readonly(str(args.get("CommandLine", "")))
    if name in STATEFUL_TOOLS:
        return True
    if name in NEUTRAL_TOOLS:
        return False
    if "browser" in name.lower():
        return bool(BROWSER_ACTION_RE.search(name))
    return bool(UNKNOWN_WRITE_RE.search(name))


def signature(name, args):
    raw = json.dumps(args, sort_keys=True, default=str) if args is not None else ""
    return "%s:%s" % (name, hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16])


# --------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------

def handle_post_tool(payload):
    call = payload.get("toolCall") or {}
    name = str(call.get("name") or "unknown")
    args = call.get("args")
    record = {
        "s": signature(name, args),
        "n": name,
        "e": 1 if payload.get("error") else 0,
        "edit": 1 if name in EDIT_TOOLS else 0,
        "m": 1 if is_state_changing(name, args, payload.get("artifactDirectoryPath")) else 0,
    }

    def update(state):
        state["calls"].append(record)
        state["calls"] = state["calls"][-MAX_CALLS:]
        state["total"] += 1
        if record["edit"]:
            state["edits"] += 1
        if record["m"]:
            state["mut"] = True
        return {}

    return with_state(payload.get("conversationId"), update)


def _utc_offset(now):
    off = now.utcoffset() or datetime.timedelta(0)
    minutes = int(off.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    minutes = abs(minutes)
    return "%s%02d:%02d" % (sign, minutes // 60, minutes % 60)


def handle_pre_invocation(payload, now=None):
    now = now or datetime.datetime.now().astimezone()
    invocation = payload.get("invocationNum")
    invocation = invocation if isinstance(invocation, int) else -1

    def update(state):
        messages = []
        if invocation == 0 or time.time() - float(state.get("anchor_t") or 0) > ANCHOR_REFRESH_SECONDS:
            messages.append(MSG_ANCHOR % (PREFIX, now.strftime("%A, %d %B %Y, %H:%M"), _utc_offset(now)))
            state["anchor_t"] = time.time()

        calls = state["calls"]
        last_edit = max([i for i, c in enumerate(calls) if c.get("edit")] or [-1])
        window = [c for c in calls[last_edit + 1:] if not c.get("edit")]
        counts = {}
        for c in window:
            counts[c["s"]] = counts.get(c["s"], 0) + 1
        if counts:
            sig, n = max(counts.items(), key=lambda kv: kv[1])
            if n >= LOOP_REPEAT:
                key = "%s@%d@%d" % (sig, state["edits"], n // LOOP_REPEAT)
                if key not in state["warned"]:
                    messages.append(MSG_LOOP % (PREFIX, sig.split(":")[0], n))
                    state["warned"] = (state["warned"] + [key])[-50:]

        recent = calls[-ERROR_STREAK:]
        if (len(recent) == ERROR_STREAK and all(c.get("e") for c in recent)
                and state["total"] - state["err_warned_at"] >= ERROR_STREAK):
            messages.append(MSG_ERRORS % (PREFIX, ERROR_STREAK))
            state["err_warned_at"] = state["total"]

        if invocation > 0 and invocation % CHECKPOINT_EVERY == 0:
            messages.append(MSG_CHECKPOINT % PREFIX)

        if not messages:
            return {}
        return {"injectSteps": [{"ephemeralMessage": "\n\n".join(messages)}]}

    return with_state(payload.get("conversationId"), update)


def handle_stop(payload):
    gate_on = os.environ.get("HONEST_FLASH_GATE", "1").strip() not in ("0", "false", "off", "no")
    reason = str(payload.get("terminationReason") or "")

    def update(state):
        if state.get("gate_pending"):
            state["gate_pending"] = False
            state["mut"] = False
            return {}
        if not gate_on:
            state["mut"] = False
            return {}
        if reason not in ("", "model_stop") or payload.get("fullyIdle") is False:
            return {}
        if state.get("mut"):
            state["gate_pending"] = True
            state["gates"] = int(state.get("gates") or 0) + 1
            return {"decision": "continue", "reason": MSG_GATE % PREFIX}
        return {}

    return with_state(payload.get("conversationId"), update)


HANDLERS = {"pre-invocation": handle_pre_invocation, "post-tool": handle_post_tool, "stop": handle_stop}


# --------------------------------------------------------------------------
# Self test
# --------------------------------------------------------------------------

def selftest():
    tmp = tempfile.mkdtemp(prefix="honest-flash-selftest-")
    old = os.environ.get("HONEST_FLASH_STATE_DIR")
    old_gate = os.environ.pop("HONEST_FLASH_GATE", None)
    os.environ["HONEST_FLASH_STATE_DIR"] = tmp
    results = []

    def check(name, cond):
        results.append((name, bool(cond)))

    try:
        base = {"conversationId": "selftest-1", "workspacePaths": ["/workspace/project"],
                "artifactDirectoryPath": "/home/u/.gemini/antigravity/brain/selftest-1",
                "modelName": "gemini-3.8-flash-medium"}
        out = handle_pre_invocation(dict(base, invocationNum=0, initialNumSteps=0))
        msg = (out.get("injectSteps") or [{}])[0].get("ephemeralMessage", "")
        check("date anchor on first invocation", PREFIX in msg and "Current date and time" in msg)
        out = handle_pre_invocation(dict(base, invocationNum=1))
        check("no message when nothing happened", out == {})

        view = {"toolCall": {"name": "view_file", "args": {"AbsolutePath": "/workspace/project/a.py"}}}
        for _ in range(3):
            check("post-tool answers {}", handle_post_tool(dict(base, stepIdx=1, **view)) == {})
        out = handle_pre_invocation(dict(base, invocationNum=2))
        msg = (out.get("injectSteps") or [{}])[0].get("ephemeralMessage", "")
        check("loop warning after 3 identical calls", "Loop warning" in msg and "view_file" in msg)
        out = handle_pre_invocation(dict(base, invocationNum=3))
        check("loop warning not repeated immediately", out == {})

        check("stop allowed when nothing changed", handle_stop(dict(base, executionNum=1,
              terminationReason="model_stop", fullyIdle=True)) == {})
        artifact = {"toolCall": {"name": "write_to_file", "args": {
            "TargetFile": "/home/u/.gemini/antigravity/brain/selftest-1/implementation_plan.md",
            "IsArtifact": True, "CodeContent": "plan"}}}
        handle_post_tool(dict(base, stepIdx=4, **artifact))
        check("artifact write does not trigger the gate", handle_stop(dict(base, executionNum=2,
              terminationReason="model_stop", fullyIdle=True)) == {})

        edit = {"toolCall": {"name": "replace_file_content", "args": {"TargetFile": "/workspace/project/a.py"}}}
        handle_post_tool(dict(base, stepIdx=5, **edit))
        out = handle_stop(dict(base, executionNum=3, terminationReason="model_stop", fullyIdle=True))
        check("gate blocks the first stop after an edit", out.get("decision") == "continue" and PREFIX in
              out.get("reason", ""))
        out = handle_stop(dict(base, executionNum=3, terminationReason="model_stop", fullyIdle=True))
        check("second stop is allowed", out == {})

        cmd = {"toolCall": {"name": "run_command", "args": {"CommandLine": "git status && ls -la", "Cwd": "/w"}}}
        handle_post_tool(dict(base, stepIdx=6, **cmd))
        check("read-only commands do not trigger the gate", handle_stop(dict(base, executionNum=4,
              terminationReason="model_stop", fullyIdle=True)) == {})
        check("npm test counts as state-changing", not command_is_readonly("npm test"))
        check("rm is state-changing", not command_is_readonly("ls && rm -rf build"))
        check("redirect is state-changing", not command_is_readonly("echo hi > notes.txt"))
        check("git log is read-only", command_is_readonly("git --no-pager log -5 --oneline"))
        check("git branch -D is state-changing", not command_is_readonly("git branch -D old"))

        for i in range(3):
            handle_post_tool(dict(base, stepIdx=7 + i, error="exit status 1",
                                  toolCall={"name": "run_command", "args": {"CommandLine": "make %d" % i}}))
        out = handle_pre_invocation(dict(base, invocationNum=4))
        msg = (out.get("injectSteps") or [{}])[0].get("ephemeralMessage", "")
        check("error streak warning", "tool calls failed" in msg)
        out = handle_pre_invocation(dict(base, invocationNum=15))
        msg = (out.get("injectSteps") or [{}])[0].get("ephemeralMessage", "")
        check("checkpoint every %d invocations" % CHECKPOINT_EVERY, "Checkpoint" in msg)

        os.environ["HONEST_FLASH_GATE"] = "0"
        handle_post_tool(dict(base, stepIdx=20, **edit))
        check("gate can be turned off", handle_stop(dict(base, executionNum=5,
              terminationReason="model_stop", fullyIdle=True)) == {})
    finally:
        if old is None:
            os.environ.pop("HONEST_FLASH_STATE_DIR", None)
        else:
            os.environ["HONEST_FLASH_STATE_DIR"] = old
        if old_gate is None:
            os.environ.pop("HONEST_FLASH_GATE", None)
        else:
            os.environ["HONEST_FLASH_GATE"] = old_gate
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [n for n, ok in results if not ok]
    for name, ok in results:
        print("%s  %s" % ("PASS" if ok else "FAIL", name))
    print("SELFTEST: %s (%d checks)" % ("OK" if not failed else "FAILED", len(results)))
    return 0 if not failed else 1


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    command = argv[0]
    if command == "selftest":
        return selftest()
    if command not in HANDLERS:
        sys.stderr.write("unknown subcommand: %s\n" % command)
        sys.stdout.write("{}")
        return 0
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
        result = HANDLERS[command](payload)
    except Exception as exc:  # never break the agent loop
        sys.stderr.write("honest-flash hook error (%s): %s\n" % (command, exc))
        result = {}
    sys.stdout.write(json.dumps(result))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
