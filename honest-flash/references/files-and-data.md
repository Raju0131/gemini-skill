# Files, documents and data playbook

A documented failure: an agent was asked to move files into a new folder. Creating the folder failed silently, the agent did not check, and each "move" overwrote the previous file. It then reported success. Check every step's result before the next one.

## Before changing files

- List the folder and read the files first. Confirm names, types and counts. Do not assume them.
- For bulk operations (rename, move, convert, organize), first make a plan table covering every file: source → destination, plus the total count. Check for collisions, where two files would get the same destination name. Show the plan to the user unless they already asked for exactly this operation.
- Work on copies, or make a backup, before changing the user's documents, unless the user said not to.
- Create destination folders first, then list them to confirm they exist before moving anything into them. On Windows, `move a b` renames or overwrites when `b` is not an existing folder. Run `scripts/path_guard.py move <sources> <destination>` first.
- Quote paths that contain spaces. Prefer absolute paths.
- Afterwards, list the destination and compare the counts with the plan. Report any mismatch.

## Writing documents

- Keep the user's requested format, language and length. Do not pad.
- Facts, figures, quotes, dates and names must come from the provided sources or from research you verified. Mark anything else as a placeholder for the user, for example `[NEEDS SOURCE]`.
- When editing an existing document, change only what was asked and keep the rest word for word. Re-read the result.

## Spreadsheets and data analysis

- Load the data with code (Python with pandas, or the right tool). Before analyzing, print the shape, columns, types, a few rows and the missing-value counts.
- Compute every number with code. Show the formula or code behind key numbers.
- Check denominators, units, filters, date ranges, duplicates and totals. Cross-check one key number a second way.
- Never invent rows, values or trends. If data is missing, say so.
- For charts, label the axes and units, and state the data source and any filters.

## Answering from provided documents

- Use only what the documents say. If the answer is not in them, say "This is not in the provided documents."
- Support each key point with a short exact quote and its location (file name, page or section).
- Begin the answer with `retrieval_status: OK`. If a document failed to load or was truncated, begin with `retrieval_status: FAILED — <which document, what happened>`.

## Personal and sensitive data

- Do not copy personal data into new places (web forms, logs, commits, reports) unless the task needs it.
- Never open password stores, SSH keys, browser profiles, `.env` files or credential files unless the user explicitly asks for that specific file.
