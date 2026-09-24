# Coding playbook

Known failure patterns this playbook prevents: claiming tests or builds passed when the output showed failures, claiming a fix that is not in the final diff, editing before the plan was approved, broad `sed` replacements that corrupted code, patch/revert loops, editing forbidden files or tests, and unrequested `git reset --hard`, rebase or commit.

## Before editing

- Find the code with search (`grep_search`, `find_by_name`) and read it. Also read the callers and the tests of anything you will change.
- Learn how this project builds and tests from its own files: README, `package.json` scripts, `Makefile`, `pyproject.toml`, `Cargo.toml`, CI workflow files. Use those exact commands. Do not invent new ones.
- Before using a library function, class, option or config key, confirm it exists in the version this project uses: check the manifest or lockfile version, then the installed package source or the official docs for that version. Never guess an API from memory.
- Before adding a dependency, confirm the exact package name exists in the registry (`npm view <name>`, `pip index versions <name>`, or the registry website). Invented package names are a real attack vector. Ask the user before adding dependencies they did not request.

## Editing

- Make the smallest change that solves the task. Keep the existing style, names, comments and formatting.
- Never leave placeholders in delivered code: `// ... existing code ...`, `# rest unchanged`, `TODO: implement`, stubs that return dummy values. Write the complete code, or report the unfinished part as not done.
- Never delete or rewrite code you were not asked to change. If a tool rewrites whole files, re-read the result and confirm every unrelated line is still there.
- Avoid blind bulk edits (`sed -i`, regex replace across many files). If one is really needed: list every match first, show the count, apply the change, search again to confirm, then view the diff.
- Do not change public interfaces, build files, CI, lockfiles or dependency versions unless the task requires it. If you do, say so in the report.
- If an edit fails twice on the same spot, stop retrying: re-read the file, then make one careful edit.

## Tests and checks

- After your last edit, run the project's real checks: tests, lint, type check, build, whichever apply. Read the output: pass and fail counts, errors, warnings.
- Report the exact scope, for example "ran `pytest tests/test_api.py`: 12 passed". Never say "all tests pass" after running a subset.
- Never make a test pass by weakening it. That means no deleting or skipping tests, no loosening assertions, no hardcoded expected values, no catching and ignoring errors, no special-casing test inputs in product code. If a test looks wrong, explain why and ask.
- Do not add suppressions (`# type: ignore`, `# noqa`, `eslint-disable`, `@ts-ignore`) to hide problems unless the user agrees.
- If tests cannot run (missing dependencies, no network, sandbox limits), report NOT RUN with the reason. Never state that they would pass.
- Before calling a failure "pre-existing" or "unrelated", prove it: run the same check on the original code (for example in a separate `git worktree` of the base commit). If you cannot prove it, say it is UNVERIFIED.

## Git

- Read-only git commands are fine: `git status`, `git diff`, `git log`, `git show`.
- Do not commit, push, pull, merge, rebase, reset, restore, clean, stash-drop, amend, delete branches or force anything unless the user asked. See [destructive-actions.md](destructive-actions.md).
- Before reporting done in a git repo, run `git status` and `git diff --stat`, then run `scripts/diff_audit.py`. Explain every changed file in your report. Deal with every HIGH or MEDIUM finding: fix it, or explain why it is intended.

## User interfaces

- If the change affects a UI, check it in the browser (see [browser.md](browser.md)) and describe what you actually saw. A successful build does not prove the UI works.

## Debugging

- Reproduce the bug first. State your hypothesis, test it, and note what you ruled out.
- After two failed fixes for the same bug, stop guessing. Gather more evidence (full error, logs, a minimal reproduction) or ask the user.
