import json
import os
import shutil
import tempfile
import unittest

from helpers import HOOKS, run


class HooksTest(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp(prefix="honest-flash-state-")
        self.base = {"conversationId": "conv-1", "workspacePaths": ["/w"], "modelName": "gemini-3.8-flash-medium",
                     "artifactDirectoryPath": "/h/.gemini/antigravity/brain/conv-1",
                     "transcriptPath": "/h/.gemini/antigravity/brain/conv-1/.system_generated/logs/transcript.jsonl"}

    def tearDown(self):
        shutil.rmtree(self.state, ignore_errors=True)

    def hook(self, sub, payload, env=None):
        e = {"HONEST_FLASH_STATE_DIR": self.state}
        e.update(env or {})
        code, out, err = run([HOOKS, sub], input_text=json.dumps(payload), env=e)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def tool(self, name, args, error=""):
        return self.hook("post-tool", dict(self.base, stepIdx=1, error=error, toolCall={"name": name, "args": args}))

    def message(self, out):
        steps = out.get("injectSteps") or []
        return steps[0]["ephemeralMessage"] if steps else ""

    def test_selftest_passes(self):
        code, out, err = run([HOOKS, "selftest"])
        self.assertEqual(code, 0, out + err)
        self.assertIn("SELFTEST: OK", out)

    def test_date_anchor_first_invocation_only(self):
        self.assertIn("Current date and time", self.message(self.hook("pre-invocation", dict(self.base, invocationNum=0))))
        self.assertEqual(self.hook("pre-invocation", dict(self.base, invocationNum=1)), {})

    def test_turn_reminder_first_call_and_after_pause(self):
        msg = self.message(self.hook("pre-invocation", dict(self.base, invocationNum=0)))
        self.assertIn("reply to the user in Bengali", msg)
        self.assertEqual(self.hook("pre-invocation", dict(self.base, invocationNum=1)), {})
        path = os.path.join(self.state, "conv-1.json")
        with open(path) as fh:
            state = json.load(fh)
        state["last_inv_t"] -= 600
        with open(path, "w") as fh:
            json.dump(state, fh)
        msg = self.message(self.hook("pre-invocation", dict(self.base, invocationNum=2)))
        self.assertIn("reply to the user in Bengali", msg)
        self.assertIn("pushed back", msg)

    def test_loop_warning_resets_after_edit(self):
        for _ in range(3):
            self.assertEqual(self.tool("grep_search", {"Query": "foo", "SearchPath": "/w"}), {})
        self.hook("pre-invocation", dict(self.base, invocationNum=0))  # consume the date anchor
        self.tool("grep_search", {"Query": "foo", "SearchPath": "/w"})
        self.tool("replace_file_content", {"TargetFile": "/w/a.py"})
        self.assertEqual(self.hook("pre-invocation", dict(self.base, invocationNum=2)), {})
        for _ in range(3):
            self.tool("view_file", {"AbsolutePath": "/w/a.py"})
        self.assertIn("Loop warning", self.message(self.hook("pre-invocation", dict(self.base, invocationNum=3))))

    def test_gate_once_per_turn(self):
        stop = dict(self.base, executionNum=1, terminationReason="model_stop", fullyIdle=True)
        self.assertEqual(self.hook("stop", stop), {})
        self.tool("run_command", {"CommandLine": "npm install left-pad", "Cwd": "/w"})
        out = self.hook("stop", stop)
        self.assertEqual(out.get("decision"), "continue")
        self.assertIn("[honest-flash]", out.get("reason", ""))
        self.assertEqual(self.hook("stop", stop), {})
        self.assertEqual(self.hook("stop", dict(stop, executionNum=2)), {})

    def test_gate_skips_errors_background_and_disabled(self):
        self.tool("write_to_file", {"TargetFile": "/w/new.py", "CodeContent": "x"})
        stop = dict(self.base, executionNum=1, terminationReason="model_stop", fullyIdle=True)
        self.assertEqual(self.hook("stop", dict(stop, terminationReason="error")), {})
        self.assertEqual(self.hook("stop", dict(stop, fullyIdle=False)), {})
        self.assertEqual(self.hook("stop", stop, env={"HONEST_FLASH_GATE": "0"}), {})
        self.assertEqual(self.hook("stop", stop), {})

    def test_conversations_are_separate(self):
        self.tool("write_to_file", {"TargetFile": "/w/new.py"})
        other = dict(self.base, conversationId="conv-2", executionNum=1, terminationReason="model_stop",
                     fullyIdle=True)
        self.assertEqual(self.hook("stop", other), {})

    def test_bad_input_never_breaks(self):
        for sub in ("pre-invocation", "post-tool", "stop"):
            code, out, _ = run([HOOKS, sub], input_text="not json", env={"HONEST_FLASH_STATE_DIR": self.state})
            self.assertEqual((code, out), (0, "{}"))
            code, out, _ = run([HOOKS, sub], input_text="", env={"HONEST_FLASH_STATE_DIR": self.state})
            self.assertEqual(code, 0)
            json.loads(out)


if __name__ == "__main__":
    unittest.main()
