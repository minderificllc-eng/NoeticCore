# NoeticCore

A Python agentic loop that bootstraps and iteratively builds an agentic application from natural-language instructions.

## Overview

NoeticCore is a self-improving agent runtime. It starts from a small set of foundational documents and a seed prompt, then repeatedly reasons, plans, and acts—primarily by reading, writing, and updating files—until the target agentic application is realized.

## Core Documents

| File | Role |
|------|------|
| `SOUL.md` | Defines the agent’s identity, values, and high-level purpose |
| `AGENT.md` | Specifies capabilities, constraints, tools, and operating procedures |
| `SEED.md` | Contains the initial instructions that drive the agentic build process |

The loop loads these files at startup and keeps them in context while it works.

## Capabilities

The agent can:

- **Load** any project file into its working context
- **Read** file contents for analysis and planning
- **Update** existing files with precise edits
- **Save** new or modified files to disk

These file-system primitives form the primary action space. The agent uses them to scaffold code, configuration, documentation, and supporting assets until the application described in `SEED.md` is complete.

## How It Works

1. Load `SOUL.md`, `AGENT.md`, and `SEED.md`.
2. Enter the agentic loop:
   - Reason about the current state of the project relative to the seed instructions.
   - Decide on the next concrete action (usually a file read, write, or edit).
   - Execute the action.
   - Observe the result and update internal state.
3. Repeat until the goals expressed in `SEED.md` are satisfied (or an explicit stop condition is reached).

## Getting Started

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=your-api-key

# Run the loop in the directory containing the core documents
python noetic_core.py
```

Ensure `SOUL.md`, `AGENT.md`, and `SEED.md` are present in the working directory before starting. Edit `SEED.md` to describe the application you want built; the included seed is a small runnable demo.

### Using a local model via Ollama

NoeticCore can target any Anthropic-compatible endpoint. Ollama 0.14.0 and
later implements the Anthropic Messages API, so a model served on your LAN
works with three environment variables:

```bash
export ANTHROPIC_BASE_URL=http://<lan-host>:11434
export ANTHROPIC_API_KEY=ollama        # required by the SDK, ignored by Ollama
export NOETIC_MODEL_ID=qwen3-coder     # any tool-capable model you have pulled
python noetic_core.py
```

Notes:

- The model must support tool calling (for example `qwen3-coder` or
  `llama3.1`); models without tool support cannot drive the loop.
- Prompt caching is an Anthropic-cloud feature and is disabled automatically
  whenever `ANTHROPIC_BASE_URL` is set.
- `NOETIC_MODEL_ID` also works against the Anthropic API to select a different
  Claude model; it defaults to `claude-opus-5`.

Run the unit tests with:

```bash
python -m pytest tests/
```

## Design Philosophy

NoeticCore treats the file system as both memory and workspace. By constraining the agent to load / read / update / save operations, the system remains transparent, auditable, and easy to interrupt or resume. All progress is visible as ordinary files on disk.

