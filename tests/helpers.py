import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "honest-flash")
SCRIPTS = os.path.join(SKILL, "scripts")
HOOKS = os.path.join(ROOT, "hooks", "honest_flash_hooks.py")
INSTALL = os.path.join(ROOT, "install.py")

for p in (SCRIPTS, os.path.join(ROOT, "hooks"), ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)


def run(argv, cwd=None, input_text=None, env=None):
    full_env = dict(os.environ)
    full_env.update(env or {})
    proc = subprocess.run([sys.executable] + argv, cwd=cwd, input=(input_text or "").encode("utf-8"),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=full_env, timeout=120)
    return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")


def git(args, cwd):
    subprocess.run(["git"] + args, cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def make_repo():
    """Temporary git repository with one commit. Returns its path."""
    path = tempfile.mkdtemp(prefix="honest-flash-test-")
    git(["init", "-q"], path)
    git(["config", "user.email", "test@example.com"], path)
    git(["config", "user.name", "Test"], path)
    git(["config", "commit.gpgsign", "false"], path)
    return path


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
