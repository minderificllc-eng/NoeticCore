"""NoeticCore: an agentic loop that builds an application from seed instructions.

The loop loads three foundation documents (SOUL.md, AGENT.md, SEED.md) into the
system prompt, then repeatedly asks the model to act through file and shell
tools until the model declares completion via the task_complete tool or the
iteration limit is reached.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import anthropic
from anthropic.types import Message, ToolUseBlock

DEFAULT_MODEL_ID = "claude-opus-5"
API_KEY_ENVIRONMENT_VARIABLE_NAME = "ANTHROPIC_API_KEY"
MODEL_ID_ENVIRONMENT_VARIABLE_NAME = "NOETIC_MODEL_ID"
BASE_URL_ENVIRONMENT_VARIABLE_NAME = "ANTHROPIC_BASE_URL"
SHELL_APPROVAL_ENVIRONMENT_VARIABLE_NAME = "NOETIC_SHELL_APPROVAL"

SHELL_APPROVAL_ALLOW = "allow"
SHELL_APPROVAL_ASK = "ask"
VALID_SHELL_APPROVAL_POLICIES = (SHELL_APPROVAL_ALLOW, SHELL_APPROVAL_ASK)

SOUL_FILE_NAME = "SOUL.md"
AGENT_FILE_NAME = "AGENT.md"
SEED_FILE_NAME = "SEED.md"
FOUNDATION_DOCUMENT_FILE_NAMES = (SOUL_FILE_NAME, AGENT_FILE_NAME, SEED_FILE_NAME)

MAXIMUM_LOOP_ITERATIONS = 100
MAXIMUM_RESPONSE_TOKENS = 64_000
SHELL_COMMAND_TIMEOUT_SECONDS = 120
MAXIMUM_TOOL_RESULT_CHARACTERS = 50_000
TOOL_RESULT_TRUNCATION_MARKER_TEMPLATE = (
    "\n[output truncated at {character_limit} characters]"
)

EXIT_CODE_SUCCESS = 0
EXIT_CODE_FAILURE = 1
EXIT_CODE_INTERRUPTED = 130

SYSTEM_PROMPT_PREAMBLE = (
    "You are NoeticCore, an autonomous build agent. Your identity, operating"
    " procedures, and goals are defined by the three foundation documents"
    " below. Work through the file and shell tools until the goals in"
    f" {SEED_FILE_NAME} are satisfied, then call task_complete."
)
INITIAL_USER_INSTRUCTION = (
    "Begin. Review the workspace and the foundation documents, then build the"
    f" application described in {SEED_FILE_NAME}. Call task_complete only when"
    " its goals are fully satisfied and verified."
)
TURN_CONTINUE_INSTRUCTION = (
    "You ended your turn without calling a tool or declaring completion."
    f" Continue working toward the goals in {SEED_FILE_NAME}, or call"
    " task_complete if they are satisfied."
)
OUTPUT_LIMIT_NOTICE = (
    "Your previous response hit the output token limit and may be incomplete."
    " Continue from where you left off."
)

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "file_read",
        "description": (
            "Read the full text content of a file in the workspace. Call this"
            " before editing any existing file so your rewrite starts from the"
            " current content."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "relative_path": {
                    "type": "string",
                    "description": "File path relative to the workspace root.",
                },
            },
            "required": ["relative_path"],
        },
    },
    {
        "name": "file_write",
        "description": (
            "Create or fully overwrite a file in the workspace with the given"
            " content. Parent directories are created automatically. Call this"
            " to save every new or modified file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "relative_path": {
                    "type": "string",
                    "description": "File path relative to the workspace root.",
                },
                "content": {
                    "type": "string",
                    "description": "The complete new content of the file.",
                },
            },
            "required": ["relative_path", "content"],
        },
    },
    {
        "name": "file_edit",
        "description": (
            "Replace one exact text occurrence in an existing file. Call this"
            " for targeted changes instead of rewriting the whole file."
            " file_read the file first; old_text must match its content"
            " exactly once, so include enough surrounding lines to be unique."
            " Use file_write for new files or full rewrites."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "relative_path": {
                    "type": "string",
                    "description": "File path relative to the workspace root.",
                },
                "old_text": {
                    "type": "string",
                    "description": (
                        "The exact existing text to replace; must appear"
                        " exactly once in the file."
                    ),
                },
                "new_text": {
                    "type": "string",
                    "description": "The replacement text.",
                },
            },
            "required": ["relative_path", "old_text", "new_text"],
        },
    },
    {
        "name": "file_list",
        "description": (
            "List every visible file in the workspace as paths relative to the"
            " workspace root. Call this to orient yourself before reading or"
            " writing files."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "shell_command_run",
        "description": (
            "Run a shell command in the workspace directory and return its"
            " stdout, stderr, and exit code. Call this to execute code, run"
            " tests, and verify that what you built actually works."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute.",
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "task_complete",
        "description": (
            f"Declare that every goal in {SEED_FILE_NAME} is satisfied and"
            " verified. Call this exactly once, as the final action; the loop"
            " stops after it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": (
                        "What was built, how it was verified, and where the"
                        " results live."
                    ),
                },
            },
            "required": ["summary"],
        },
    },
]


class NoeticCoreError(Exception):
    """Root of the NoeticCore exception hierarchy."""


class ConfigurationError(NoeticCoreError):
    """The environment or workspace is not set up to run the loop."""


class FileMissingError(NoeticCoreError):
    """A required file does not exist in the workspace."""

    def __init__(self, relative_path: str) -> None:
        super().__init__(f"File does not exist: {relative_path}")
        self.relative_path = relative_path


class FileReadError(NoeticCoreError):
    """A file exists but could not be read as text."""

    def __init__(self, relative_path: str) -> None:
        super().__init__(f"File could not be read: {relative_path}")
        self.relative_path = relative_path


class FileWriteError(NoeticCoreError):
    """A file could not be written."""

    def __init__(self, relative_path: str) -> None:
        super().__init__(f"File could not be written: {relative_path}")
        self.relative_path = relative_path


class FileEditTargetError(NoeticCoreError):
    """An edit's target text did not match exactly once in the file."""

    def __init__(self, relative_path: str, occurrence_count: int) -> None:
        super().__init__(
            f"Edit target matched {occurrence_count} times in {relative_path};"
            " it must match exactly once. Provide a longer, unique old_text."
        )
        self.relative_path = relative_path
        self.occurrence_count = occurrence_count


class OperatorDeclinedError(NoeticCoreError):
    """The human operator declined a proposed shell command."""

    def __init__(self, command: str) -> None:
        super().__init__(
            f"Operator declined this command: {command}."
            " Explain your intent or try a different approach."
        )
        self.command = command


class PathOutsideWorkspaceError(NoeticCoreError):
    """A path resolves to a location outside the workspace directory."""

    def __init__(self, relative_path: str) -> None:
        super().__init__(
            f"Path escapes the workspace and was refused: {relative_path}"
        )
        self.relative_path = relative_path


class ShellCommandTimeoutError(NoeticCoreError):
    """A shell command exceeded the execution time limit."""

    def __init__(self, command: str, timeout_seconds: int) -> None:
        super().__init__(
            f"Command exceeded the {timeout_seconds}-second limit: {command}"
        )
        self.command = command
        self.timeout_seconds = timeout_seconds


class AgentResponseError(NoeticCoreError):
    """The model returned a response the loop cannot act on."""


class IterationLimitReachedError(NoeticCoreError):
    """The loop reached its iteration cap without a completion declaration."""

    def __init__(self, iteration_limit: int) -> None:
        super().__init__(
            f"Stopped after {iteration_limit} iterations without task_complete."
            " Raise MAXIMUM_LOOP_ITERATIONS or simplify the seed goals."
        )
        self.iteration_limit = iteration_limit


@dataclass(frozen=True)
class ModelConfiguration:
    """Which model to talk to, and over which endpoint.

    base_url is None for the Anthropic cloud API; any other value is an
    Anthropic-compatible endpoint (for example an Ollama server on the LAN).
    Prompt caching is an Anthropic-cloud feature, so it is enabled only there.
    shell_approval_policy is one of VALID_SHELL_APPROVAL_POLICIES and controls
    whether each shell command needs operator approval before it runs.
    """

    model_id: str
    base_url: str | None
    prompt_caching_is_enabled: bool
    shell_approval_policy: str


def shell_approval_policy_load() -> str:
    """Return the validated shell approval policy from the environment.
    Raises ConfigurationError for an unrecognized value."""
    shell_approval_policy = os.environ.get(
        SHELL_APPROVAL_ENVIRONMENT_VARIABLE_NAME, SHELL_APPROVAL_ALLOW
    )
    if shell_approval_policy in VALID_SHELL_APPROVAL_POLICIES:
        return shell_approval_policy
    raise ConfigurationError(
        f"{SHELL_APPROVAL_ENVIRONMENT_VARIABLE_NAME} is set to"
        f" '{shell_approval_policy}'; valid values are"
        f" {', '.join(VALID_SHELL_APPROVAL_POLICIES)}."
    )


def model_configuration_load() -> ModelConfiguration:
    """Return the model configuration read from the environment."""
    model_id = os.environ.get(MODEL_ID_ENVIRONMENT_VARIABLE_NAME, DEFAULT_MODEL_ID)
    base_url = os.environ.get(BASE_URL_ENVIRONMENT_VARIABLE_NAME) or None
    return ModelConfiguration(
        model_id=model_id,
        base_url=base_url,
        prompt_caching_is_enabled=base_url is None,
        shell_approval_policy=shell_approval_policy_load(),
    )


def shell_command_approve(command: str) -> bool:
    """Show the proposed command to the operator and return their decision."""
    print(f"\nProposed shell command:\n  {command}")
    operator_answer = input("Run this command? [y/N] ")
    return operator_answer.strip().lower() == "y"


def model_target_describe(model_configuration: ModelConfiguration) -> str:
    """Return a one-line description of where requests will be sent."""
    endpoint_description = (
        "the Anthropic API"
        if model_configuration.base_url is None
        else model_configuration.base_url
    )
    target_description = (
        f"model {model_configuration.model_id} via {endpoint_description}"
    )
    if model_configuration.shell_approval_policy == SHELL_APPROVAL_ASK:
        return f"{target_description} (shell commands require approval)"
    return target_description


def system_prompt_block_build(
    system_prompt: str, prompt_caching_is_enabled: bool
) -> dict[str, Any]:
    """Return the system prompt as a content block, with a cache breakpoint
    only when the endpoint supports prompt caching."""
    system_prompt_block: dict[str, Any] = {"type": "text", "text": system_prompt}
    if prompt_caching_is_enabled:
        system_prompt_block["cache_control"] = {"type": "ephemeral"}
    return system_prompt_block


@dataclass(frozen=True)
class ShellCommandResult:
    """The captured outcome of one shell command."""

    standard_output: str
    standard_error: str
    exit_code: int

    def report_format(self) -> str:
        """Return the result as text the model can read."""
        report_sections: list[str] = []
        if self.standard_output:
            report_sections.append(f"stdout:\n{self.standard_output}")
        if self.standard_error:
            report_sections.append(f"stderr:\n{self.standard_error}")
        report_sections.append(f"exit code: {self.exit_code}")
        return "\n\n".join(report_sections)


@dataclass(frozen=True)
class ToolCallResult:
    """The outcome of one dispatched tool call, in tool_result form."""

    content: str
    is_error: bool


class FileStore:
    """Filesystem access confined to a single workspace directory."""

    def __init__(self, workspace_directory: Path) -> None:
        self.workspace_directory = workspace_directory.resolve()

    def path_resolve(self, relative_path: str) -> Path:
        """Return the absolute path for relative_path, verified to lie inside
        the workspace. Raises PathOutsideWorkspaceError when it escapes."""
        candidate_path = (self.workspace_directory / relative_path).resolve()
        if not candidate_path.is_relative_to(self.workspace_directory):
            raise PathOutsideWorkspaceError(relative_path)
        return candidate_path

    def file_read(self, relative_path: str) -> str:
        """Return the file's text. Raises FileMissingError or FileReadError."""
        file_path = self.path_resolve(relative_path)
        if not file_path.is_file():
            raise FileMissingError(relative_path)
        try:
            return file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as read_error:
            raise FileReadError(relative_path) from read_error

    def file_write(self, relative_path: str, content: str) -> None:
        """Create or overwrite the file with content. Raises FileWriteError."""
        file_path = self.path_resolve(relative_path)
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
        except OSError as write_error:
            raise FileWriteError(relative_path) from write_error

    def file_edit(
        self, relative_path: str, old_text: str, new_text: str
    ) -> None:
        """Replace old_text with new_text in the file. Raises
        FileEditTargetError unless old_text matches exactly once, plus the
        file_read and file_write errors."""
        current_content = self.file_read(relative_path)
        occurrence_count = current_content.count(old_text)
        if occurrence_count != 1:
            raise FileEditTargetError(relative_path, occurrence_count)
        updated_content = current_content.replace(old_text, new_text)
        self.file_write(relative_path, updated_content)

    def file_list(self) -> list[str]:
        """Return every visible file as a sorted workspace-relative path."""
        all_file_paths = (
            path for path in self.workspace_directory.rglob("*") if path.is_file()
        )
        visible_file_paths = (
            path for path in all_file_paths if not self.path_is_hidden(path)
        )
        return sorted(
            str(path.relative_to(self.workspace_directory))
            for path in visible_file_paths
        )

    def path_is_hidden(self, absolute_path: Path) -> bool:
        """Return whether any component of the workspace-relative path is a
        dot-directory or dot-file (for example .git)."""
        relative_parts = absolute_path.relative_to(self.workspace_directory).parts
        return any(part.startswith(".") for part in relative_parts)


class ShellExecutor:
    """Runs shell commands inside the workspace directory with a hard timeout."""

    def __init__(self, workspace_directory: Path) -> None:
        self.workspace_directory = workspace_directory

    def shell_command_run(self, command: str) -> ShellCommandResult:
        """Run the command and capture its output. Raises
        ShellCommandTimeoutError when the time limit is exceeded."""
        try:
            completed_process = subprocess.run(
                command,
                shell=True,
                cwd=self.workspace_directory,
                capture_output=True,
                text=True,
                timeout=SHELL_COMMAND_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as timeout_error:
            raise ShellCommandTimeoutError(
                command, SHELL_COMMAND_TIMEOUT_SECONDS
            ) from timeout_error
        return ShellCommandResult(
            standard_output=completed_process.stdout,
            standard_error=completed_process.stderr,
            exit_code=completed_process.returncode,
        )


def tool_result_content_truncate(content: str) -> str:
    """Return content unchanged, or truncated with a visible marker when it
    exceeds MAXIMUM_TOOL_RESULT_CHARACTERS."""
    if len(content) <= MAXIMUM_TOOL_RESULT_CHARACTERS:
        return content
    truncation_marker = TOOL_RESULT_TRUNCATION_MARKER_TEMPLATE.format(
        character_limit=MAXIMUM_TOOL_RESULT_CHARACTERS
    )
    return content[:MAXIMUM_TOOL_RESULT_CHARACTERS] + truncation_marker


def foundation_document_section_format(file_name: str, content: str) -> str:
    """Return one foundation document as a labeled system-prompt section."""
    return f"## {file_name}\n\n{content.strip()}"


def refusal_reason_describe(response: Message) -> str:
    """Return a readable description of a refusal response."""
    stop_details = getattr(response, "stop_details", None)
    if stop_details is None:
        return "The model declined the request (stop_reason: refusal)."
    return (
        "The model declined the request"
        f" (category: {stop_details.category};"
        f" explanation: {stop_details.explanation})."
    )


def iteration_progress_print(iteration_number: int, response: Message) -> None:
    """Print one line of loop progress plus any assistant commentary."""
    tool_names = [
        block.name for block in response.content if block.type == "tool_use"
    ]
    tool_summary = ", ".join(tool_names) if tool_names else "no tool calls"
    print(f"[iteration {iteration_number}] tools: {tool_summary}")
    for block in response.content:
        if block.type == "text" and block.text.strip():
            print(block.text.strip())


class AgentLoop:
    """Owns the model conversation and drives it until completion."""

    def __init__(
        self,
        anthropic_client: anthropic.Anthropic,
        model_configuration: ModelConfiguration,
        file_store: FileStore,
        shell_executor: ShellExecutor,
    ) -> None:
        self.anthropic_client = anthropic_client
        self.model_configuration = model_configuration
        self.file_store = file_store
        self.shell_executor = shell_executor
        self.system_prompt = self.foundation_documents_load()
        self.conversation_messages: list[dict[str, Any]] = []
        self.completion_summary: str | None = None
        self.tool_handlers: dict[str, Callable[[dict[str, Any]], str]] = {
            "file_read": self.tool_file_read,
            "file_write": self.tool_file_write,
            "file_edit": self.tool_file_edit,
            "file_list": self.tool_file_list,
            "shell_command_run": self.tool_shell_command_run,
            "task_complete": self.tool_task_complete,
        }

    def foundation_documents_load(self) -> str:
        """Return the system prompt assembled from the foundation documents.
        Raises FileMissingError or FileReadError for an unreadable document."""
        document_sections = [
            foundation_document_section_format(
                file_name, self.file_store.file_read(file_name)
            )
            for file_name in FOUNDATION_DOCUMENT_FILE_NAMES
        ]
        return SYSTEM_PROMPT_PREAMBLE + "\n\n" + "\n\n".join(document_sections)

    def loop_run(self) -> str:
        """Drive the agentic loop and return the model's completion summary.
        Raises AgentResponseError on refusal and IterationLimitReachedError
        when the iteration cap is reached without task_complete."""
        self.conversation_messages.append(
            {"role": "user", "content": INITIAL_USER_INSTRUCTION}
        )
        for iteration_number in range(1, MAXIMUM_LOOP_ITERATIONS + 1):
            response = self.model_response_request()
            iteration_progress_print(iteration_number, response)
            self.conversation_messages.append(
                {"role": "assistant", "content": response.content}
            )
            if response.stop_reason == "refusal":
                raise AgentResponseError(refusal_reason_describe(response))
            if response.stop_reason == "pause_turn":
                continue
            tool_result_blocks = self.tool_calls_execute(response)
            if self.completion_summary is not None:
                return self.completion_summary
            next_user_content = tool_result_blocks or self.turn_nudge_build(
                response
            )
            self.conversation_messages.append(
                {"role": "user", "content": next_user_content}
            )
        raise IterationLimitReachedError(MAXIMUM_LOOP_ITERATIONS)

    def model_response_request(self) -> Message:
        """Send the conversation to the model and return the full response."""
        with self.anthropic_client.messages.stream(
            model=self.model_configuration.model_id,
            max_tokens=MAXIMUM_RESPONSE_TOKENS,
            system=[
                system_prompt_block_build(
                    self.system_prompt,
                    self.model_configuration.prompt_caching_is_enabled,
                )
            ],
            tools=TOOL_DEFINITIONS,
            messages=self.conversation_messages,
        ) as response_stream:
            return response_stream.get_final_message()

    def tool_calls_execute(self, response: Message) -> list[dict[str, Any]]:
        """Execute every tool call in the response and return all tool_result
        blocks, in order, for a single user message."""
        tool_use_blocks = [
            block for block in response.content if block.type == "tool_use"
        ]
        return [self.tool_result_block_build(block) for block in tool_use_blocks]

    def tool_result_block_build(
        self, tool_use_block: ToolUseBlock
    ) -> dict[str, Any]:
        """Dispatch one tool call and wrap its outcome as a tool_result block."""
        tool_call_result = self.tool_call_dispatch(
            tool_use_block.name, tool_use_block.input
        )
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_block.id,
            "content": tool_call_result.content,
            "is_error": tool_call_result.is_error,
        }

    def tool_call_dispatch(
        self, tool_name: str, tool_input: dict[str, Any]
    ) -> ToolCallResult:
        """Run the named tool handler. Every failure becomes an error result
        the model can read and recover from; nothing fails silently."""
        tool_handler = self.tool_handlers.get(tool_name)
        if tool_handler is None:
            known_tool_names = ", ".join(sorted(self.tool_handlers))
            return ToolCallResult(
                content=(
                    f"Unknown tool: {tool_name}."
                    f" Available tools: {known_tool_names}."
                ),
                is_error=True,
            )
        try:
            result_content = tool_handler(tool_input)
        except KeyError as missing_argument_error:
            return ToolCallResult(
                content=(
                    f"Tool {tool_name} was called without required argument"
                    f" {missing_argument_error}."
                ),
                is_error=True,
            )
        except NoeticCoreError as tool_error:
            return ToolCallResult(
                content=f"Tool {tool_name} failed: {tool_error}",
                is_error=True,
            )
        return ToolCallResult(
            content=tool_result_content_truncate(result_content),
            is_error=False,
        )

    def turn_nudge_build(self, response: Message) -> str:
        """Return the follow-up instruction for a turn that made no tool calls."""
        if response.stop_reason == "max_tokens":
            return OUTPUT_LIMIT_NOTICE
        return TURN_CONTINUE_INSTRUCTION

    def tool_file_read(self, tool_input: dict[str, Any]) -> str:
        return self.file_store.file_read(tool_input["relative_path"])

    def tool_file_write(self, tool_input: dict[str, Any]) -> str:
        relative_path = tool_input["relative_path"]
        content = tool_input["content"]
        self.file_store.file_write(relative_path, content)
        return f"Wrote {len(content)} characters to {relative_path}."

    def tool_file_edit(self, tool_input: dict[str, Any]) -> str:
        relative_path = tool_input["relative_path"]
        self.file_store.file_edit(
            relative_path, tool_input["old_text"], tool_input["new_text"]
        )
        return f"Applied the edit to {relative_path}."

    def tool_file_list(self, tool_input: dict[str, Any]) -> str:
        file_paths = self.file_store.file_list()
        if not file_paths:
            return "The workspace contains no files."
        return "\n".join(file_paths)

    def tool_shell_command_run(self, tool_input: dict[str, Any]) -> str:
        command = tool_input["command"]
        approval_is_required = (
            self.model_configuration.shell_approval_policy == SHELL_APPROVAL_ASK
        )
        if approval_is_required and not shell_command_approve(command):
            raise OperatorDeclinedError(command)
        command_result = self.shell_executor.shell_command_run(command)
        return command_result.report_format()

    def tool_task_complete(self, tool_input: dict[str, Any]) -> str:
        self.completion_summary = tool_input["summary"]
        return "Completion acknowledged."


def api_key_verify() -> None:
    """Raise ConfigurationError when the Anthropic API key is not configured."""
    if os.environ.get(API_KEY_ENVIRONMENT_VARIABLE_NAME):
        return
    raise ConfigurationError(
        f"The {API_KEY_ENVIRONMENT_VARIABLE_NAME} environment variable is not"
        " set. Export your Anthropic API key before running NoeticCore."
    )


def foundation_documents_verify(file_store: FileStore) -> None:
    """Raise ConfigurationError naming every missing foundation document."""
    missing_file_names = [
        file_name
        for file_name in FOUNDATION_DOCUMENT_FILE_NAMES
        if not file_store.path_resolve(file_name).is_file()
    ]
    if not missing_file_names:
        return
    raise ConfigurationError(
        "Missing foundation documents in the workspace:"
        f" {', '.join(missing_file_names)}. All of"
        f" {', '.join(FOUNDATION_DOCUMENT_FILE_NAMES)} must exist."
    )


def application_build_run(workspace_directory: Path) -> str:
    """Validate the environment, then run the loop to completion."""
    api_key_verify()
    model_configuration = model_configuration_load()
    print(f"NoeticCore starting: {model_target_describe(model_configuration)}")
    file_store = FileStore(workspace_directory)
    foundation_documents_verify(file_store)
    shell_executor = ShellExecutor(workspace_directory)
    agent_loop = AgentLoop(
        anthropic.Anthropic(base_url=model_configuration.base_url),
        model_configuration,
        file_store,
        shell_executor,
    )
    return agent_loop.loop_run()


def main() -> int:
    """Run NoeticCore in the current directory and report the outcome."""
    try:
        completion_summary = application_build_run(Path.cwd())
    except KeyboardInterrupt:
        print("Interrupted by user; stopping.", file=sys.stderr)
        return EXIT_CODE_INTERRUPTED
    except NoeticCoreError as core_error:
        print(f"NoeticCore stopped: {core_error}", file=sys.stderr)
        return EXIT_CODE_FAILURE
    except anthropic.AuthenticationError as authentication_error:
        print(
            "Anthropic API authentication failed: check"
            f" {API_KEY_ENVIRONMENT_VARIABLE_NAME}."
            f" Details: {authentication_error.message}",
            file=sys.stderr,
        )
        return EXIT_CODE_FAILURE
    except anthropic.RateLimitError:
        print(
            "Anthropic API rate limit reached. Wait and rerun; the SDK already"
            " retried with backoff.",
            file=sys.stderr,
        )
        return EXIT_CODE_FAILURE
    except anthropic.APIStatusError as api_status_error:
        print(
            "Anthropic API request failed with status"
            f" {api_status_error.status_code}: {api_status_error.message}",
            file=sys.stderr,
        )
        return EXIT_CODE_FAILURE
    except anthropic.APIConnectionError:
        print(
            "Could not reach the model endpoint: check the network connection"
            f" (and {BASE_URL_ENVIRONMENT_VARIABLE_NAME}, if set), then rerun.",
            file=sys.stderr,
        )
        return EXIT_CODE_FAILURE
    print("\nNoeticCore finished. Completion summary:")
    print(completion_summary)
    return EXIT_CODE_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
