# SEED

## Goal

Build a command-line todo application in this workspace.

## Requirements

1. Create `todo.py`, runnable as `python todo.py <command>`, supporting:
   - `add "<text>"` — add a task and print its assigned number
   - `list` — print every task with its number and status (open or done)
   - `done <number>` — mark the numbered task done; error clearly if the
     number does not exist
2. Tasks persist between runs in a JSON file named `todo_data.json` in the
   workspace. A missing or empty data file means "no tasks yet", not a crash.
3. Invalid usage (unknown command, missing argument, malformed number) prints
   a clear usage message and exits with a nonzero status — never a traceback.
4. Create `test_todo.py` with pytest tests covering: adding, listing,
   completing, completing a nonexistent number, and first-run behavior with no
   data file. Tests must not touch a real user's data — use a temporary
   directory.
5. The code follows the conventions in STYLE_GUIDE.md.

## Done means

`python -m pytest test_todo.py` passes with every test green, and a manual
`add` / `list` / `done` sequence via the shell behaves as specified.
