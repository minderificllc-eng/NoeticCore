# NoeticCore Style and Coding Guide

This guide defines the required style for all code in this repository. Every rule
exists to serve one goal: code that a reader can understand correctly on the
first pass, without tribal knowledge, and that fails loudly and informatively
when something goes wrong.

## Core Principles

1. **Flat over nested.** Control flow reads top to bottom; the happy path is
   never indented inside error handling.
2. **Names carry the documentation.** Identifiers are fully spelled out and
   precise enough that most comments become unnecessary.
3. **Composable units.** Small functions and classes with single
   responsibilities, combined explicitly — never entangled.
4. **No magic values.** Every literal that carries meaning has a name.
5. **No hacks.** Friction is a signal to extend the design, not to route
   around it.
6. **No silent failures, no crashes.** Every failure mode is either handled
   meaningfully or converted into a clear, contextual error.

---

## Naming

### Fully spelled out, always

No abbreviations, no single letters (loop indices included), no acronyms unless
they are universally standard in the domain (`json`, `http`, `id`).

```python
# Wrong
def proc_msg(m, ctx): ...
for i, f in enumerate(fs): ...

# Right
def message_process(message, conversation_context): ...
for file_index, file_path in enumerate(file_paths): ...
```

### Functions: `noun_verb`, generic to specific

Function names lead with the noun being operated on, ordered from the most
generic concept to the most specific, and end with the verb. This groups
related operations together alphabetically and makes call sites read as
"subject, then action."

```python
# Wrong (verb-first)
load_soul_document()
read_file()
append_message_to_context()

# Right (noun_verb, generic → specific)
document_soul_load()
file_read()
context_message_append()
```

The noun chain narrows left to right: `file_read` (any file),
`file_json_read` (a JSON file), `config_file_json_read` would be wrong —
`config` is the generic domain, so it leads: `config_json_read`.

### Variables and constants

- Variables are nouns or noun phrases: `retry_attempt_count`, not `n` or `cnt`.
- Booleans read as assertions: `is_file_present`, `has_reached_stop_condition`.
- Constants are `UPPER_SNAKE_CASE` and include units where applicable:
  `REQUEST_TIMEOUT_SECONDS`, `MAXIMUM_CONTEXT_TOKENS`.
- Classes are `PascalCase` nouns: `AgentLoop`, `DocumentStore`.

---

## Control Flow: Never-Nester Style

Maximum nesting depth inside a function body is **two levels**. Reaching a
third level means the function must be restructured, using these tools in
order of preference:

1. **Guard clauses.** Validate and return/raise early; the happy path stays at
   the left margin.
2. **Inversion.** Flip the condition so the short, exceptional branch exits
   first.
3. **Extraction.** Pull the inner block into a named function — the name it
   requires is documentation you were about to omit.

```python
# Wrong
def document_load(file_path):
    if file_path.exists():
        if file_path.suffix == ".md":
            content = file_path.read_text()
            if content:
                return content
            else:
                return None
        else:
            return None

# Right
def document_load(file_path: Path) -> str:
    if not file_path.exists():
        raise DocumentNotFoundError(file_path)
    if file_path.suffix != MARKDOWN_FILE_SUFFIX:
        raise DocumentFormatError(file_path, expected_suffix=MARKDOWN_FILE_SUFFIX)
    content = file_path.read_text(encoding="utf-8")
    if not content.strip():
        raise DocumentEmptyError(file_path)
    return content
```

Additional rules:

- No `else` after a branch that returns or raises.
- Loop bodies that need more than a few lines become a named function called
  from the loop.
- Prefer returning early over accumulating state flags
  (`found = True` … checked later) that force the reader to track state.

---

## No Magic Numbers or Strings

Any literal whose meaning is not self-evident from its immediate context gets
a named constant at module level (or class level when scoped to a class).
This includes numbers, strings used as keys or sentinels, file names, and
format markers.

```python
# Wrong
time.sleep(2 ** attempt)
if len(context) > 180_000: ...
files = ["SOUL.md", "AGENT.md", "SEED.md"]

# Right
RETRY_BACKOFF_BASE_SECONDS = 2
MAXIMUM_CONTEXT_TOKENS = 180_000
FOUNDATION_DOCUMENT_FILE_NAMES = ("SOUL.md", "AGENT.md", "SEED.md")
```

`0`, `1`, and empty collections are exempt when used in their ordinary
structural sense (indexing, counting from zero, emptiness checks).

---

## Composability and Self-Documentation

- Each function does **one thing**, stated by its name. If describing the
  function honestly requires "and," split it.
- Prefer **pure functions** (inputs → outputs, no hidden state) for logic;
  confine side effects (file writes, network calls, printing) to thin,
  clearly named boundary functions.
- Pass dependencies in explicitly (parameters or constructor injection);
  never reach out to globals or module-level mutable state.
- **Type hints are mandatory** on every function signature and public
  attribute.
- Docstrings state the contract — what is required, what is guaranteed, what
  is raised — not a restatement of the code.
- Comments are reserved for *why*: constraints, invariants, and non-obvious
  consequences that the code cannot express. A comment explaining *what* a
  line does means the line needs a better name, not a comment.

---

## Object Orientation — Where It Earns Its Place

Use a class when state and the behavior that governs it belong together
(`AgentLoop`, `DocumentStore`, `ConversationContext`). Use plain functions
when there is no state to protect. Use `@dataclass(frozen=True)` for
pure data records.

- Prefer **composition over inheritance**. Inheritance is reserved for true
  is-a relationships with a stable base contract (e.g., an exception
  hierarchy, an abstract tool interface).
- Keep hierarchies at most **one abstract layer deep**.
- Every class has a single reason to change. A class named `Manager`,
  `Helper`, or `Utils` is a design smell — name what it actually is, or
  dissolve it into functions.

---

## No Hacks or Workarounds

When the current design makes something awkward — a parameter that has to be
threaded through five layers, a special case bolted onto a general function, a
sleep added to dodge a race — the awkwardness is a design report, not an
obstacle to sidestep.

**Rule:** extend the abstraction so the needed capability is a first-class,
named part of the system. If the extension is too large for the current
change, stop and surface it; do not land the workaround. Code containing
`# HACK`, `# TODO: fix properly`, copy-pasted near-duplicates, or
special-case flags does not merge.

---

## Error Handling: No Silent Failures, No Crashes

Every operation that can fail — file I/O, parsing, network calls, external
processes, user input — is handled according to these rules:

1. **Validate at the boundary.** Check inputs where they enter the system
   (file loading, API responses, user commands) and raise immediately with a
   specific error. Interior code may then rely on validated data.
2. **A domain exception hierarchy.** Define one root exception per subsystem
   (e.g., `NoeticCoreError`) with specific subclasses
   (`DocumentNotFoundError`, `AgentActionError`). Raise these — never bare
   `Exception` — and include the failing values in the message.
3. **Catch narrowly, never silently.**
   - Never `except:` or `except Exception: pass`.
   - Catch the specific exception you can genuinely handle; wrap and re-raise
     (`raise DomainError(...) from original_error`) when crossing a subsystem
     boundary, preserving the cause chain.
   - If you cannot handle it meaningfully, let it propagate — the top level
     handles it.
4. **One top-level handler.** The application entry point catches the domain
   root exception, reports it clearly (what failed, with which inputs, and
   what the user can do), and exits with a nonzero status. Unexpected
   exceptions are reported with full traceback — visibly, never swallowed.
5. **Functions never signal failure ambiguously.** No returning `None`,
   `-1`, or empty strings to mean "it failed." Return a valid value or raise.
   If absence is a *normal* outcome, make it explicit in the type
   (`Optional[...]` with the meaning documented) — reserved for genuine
   absence, not error suppression.
6. **Recoverable failures recover deliberately.** Retries have a named
   maximum attempt count and backoff constant, log each failure, and raise a
   specific exception when exhausted — partial progress is never discarded
   silently.

```python
# Wrong
try:
    content = open(path).read()
except:
    content = ""

# Right
def document_file_read(file_path: Path) -> str:
    """Return the document's text. Raises DocumentNotFoundError or DocumentReadError."""
    if not file_path.exists():
        raise DocumentNotFoundError(file_path)
    try:
        return file_path.read_text(encoding="utf-8")
    except OSError as read_error:
        raise DocumentReadError(file_path) from read_error
```

---

## Review Checklist

Before merging, confirm:

- [ ] No function nests deeper than two levels.
- [ ] Every identifier is fully spelled out; functions are `noun_verb`,
      generic to specific.
- [ ] No unexplained literals; constants are named, with units.
- [ ] Every function has type hints and does exactly one thing.
- [ ] Side effects are confined to named boundary functions.
- [ ] Classes hold state plus its governing behavior — nothing else.
- [ ] No workarounds, duplicated logic, or `# HACK`/`# TODO: fix properly`.
- [ ] No bare or broad excepts; no failure signaled by `None`/sentinel;
      all raised errors carry context and cause chains.
- [ ] The code reads correctly top to bottom without needing comments to
      explain *what* it does.
