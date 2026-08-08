"""Unit tests for the non-API parts of noetic_core.

The Anthropic client is never contacted: AgentLoop tests inject a placeholder
client object and exercise only construction and tool dispatch.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from noetic_core import (
    DEFAULT_MODEL_ID,
    FOUNDATION_DOCUMENT_FILE_NAMES,
    MAXIMUM_TOOL_RESULT_CHARACTERS,
    SHELL_APPROVAL_ALLOW,
    SHELL_APPROVAL_ASK,
    AgentLoop,
    ConfigurationError,
    FileEditTargetError,
    FileMissingError,
    FileStore,
    ModelConfiguration,
    PathOutsideWorkspaceError,
    ShellCommandResult,
    ShellExecutor,
    api_key_verify,
    foundation_documents_verify,
    model_configuration_load,
    system_prompt_block_build,
    tool_result_content_truncate,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def file_store(workspace: Path) -> FileStore:
    return FileStore(workspace)


def agent_loop_build(
    workspace: Path, file_store: FileStore, shell_approval_policy: str
) -> AgentLoop:
    for file_name in FOUNDATION_DOCUMENT_FILE_NAMES:
        (workspace / file_name).write_text(f"# {file_name}\n", encoding="utf-8")
    placeholder_client = object()
    model_configuration = ModelConfiguration(
        model_id=DEFAULT_MODEL_ID,
        base_url=None,
        prompt_caching_is_enabled=True,
        shell_approval_policy=shell_approval_policy,
    )
    return AgentLoop(
        placeholder_client,
        model_configuration,
        file_store,
        ShellExecutor(workspace),
    )


@pytest.fixture
def agent_loop(workspace: Path, file_store: FileStore) -> AgentLoop:
    return agent_loop_build(workspace, file_store, SHELL_APPROVAL_ALLOW)


@pytest.fixture
def supervised_agent_loop(workspace: Path, file_store: FileStore) -> AgentLoop:
    return agent_loop_build(workspace, file_store, SHELL_APPROVAL_ASK)


class TestFileStore:
    def test_file_write_then_read_round_trips(self, file_store: FileStore) -> None:
        file_store.file_write("notes/example.txt", "hello")
        assert file_store.file_read("notes/example.txt") == "hello"

    def test_file_read_missing_file_raises(self, file_store: FileStore) -> None:
        with pytest.raises(FileMissingError):
            file_store.file_read("absent.txt")

    def test_parent_traversal_path_is_refused(self, file_store: FileStore) -> None:
        with pytest.raises(PathOutsideWorkspaceError):
            file_store.path_resolve("../escape.txt")

    def test_absolute_path_outside_workspace_is_refused(
        self, file_store: FileStore
    ) -> None:
        with pytest.raises(PathOutsideWorkspaceError):
            file_store.path_resolve("/etc/passwd")

    def test_file_edit_replaces_unique_occurrence(
        self, file_store: FileStore
    ) -> None:
        file_store.file_write("code.py", "value = 1\nother = 2\n")
        file_store.file_edit("code.py", "value = 1", "value = 10")
        assert file_store.file_read("code.py") == "value = 10\nother = 2\n"

    def test_file_edit_zero_matches_raises(self, file_store: FileStore) -> None:
        file_store.file_write("code.py", "value = 1\n")
        with pytest.raises(FileEditTargetError) as error_info:
            file_store.file_edit("code.py", "absent text", "replacement")
        assert error_info.value.occurrence_count == 0

    def test_file_edit_multiple_matches_raises(
        self, file_store: FileStore
    ) -> None:
        file_store.file_write("code.py", "x = 1\nx = 1\n")
        with pytest.raises(FileEditTargetError) as error_info:
            file_store.file_edit("code.py", "x = 1", "x = 2")
        assert error_info.value.occurrence_count == 2

    def test_file_edit_missing_file_raises(self, file_store: FileStore) -> None:
        with pytest.raises(FileMissingError):
            file_store.file_edit("absent.py", "old", "new")

    def test_file_list_excludes_hidden_paths(
        self, workspace: Path, file_store: FileStore
    ) -> None:
        file_store.file_write("visible.txt", "content")
        hidden_directory = workspace / ".git"
        hidden_directory.mkdir()
        (hidden_directory / "config").write_text("content", encoding="utf-8")
        assert file_store.file_list() == ["visible.txt"]


class TestShellExecutor:
    def test_successful_command_captures_output(self, workspace: Path) -> None:
        command_result = ShellExecutor(workspace).shell_command_run("echo hello")
        assert command_result.standard_output.strip() == "hello"
        assert command_result.exit_code == 0

    def test_failing_command_reports_nonzero_exit_code(
        self, workspace: Path
    ) -> None:
        command_result = ShellExecutor(workspace).shell_command_run("false")
        assert command_result.exit_code != 0

    def test_report_format_includes_exit_code(self) -> None:
        command_result = ShellCommandResult(
            standard_output="out", standard_error="err", exit_code=3
        )
        report = command_result.report_format()
        assert "out" in report
        assert "err" in report
        assert "exit code: 3" in report


class TestToolResultTruncation:
    def test_short_content_is_unchanged(self) -> None:
        assert tool_result_content_truncate("short") == "short"

    def test_oversized_content_is_truncated_with_marker(self) -> None:
        oversized_content = "x" * (MAXIMUM_TOOL_RESULT_CHARACTERS + 100)
        truncated_content = tool_result_content_truncate(oversized_content)
        assert len(truncated_content) < len(oversized_content)
        assert "truncated" in truncated_content


class TestAgentLoopDispatch:
    def test_unknown_tool_returns_error_result(self, agent_loop: AgentLoop) -> None:
        tool_call_result = agent_loop.tool_call_dispatch("nonexistent_tool", {})
        assert tool_call_result.is_error
        assert "Unknown tool" in tool_call_result.content

    def test_missing_argument_returns_error_result(
        self, agent_loop: AgentLoop
    ) -> None:
        tool_call_result = agent_loop.tool_call_dispatch("file_read", {})
        assert tool_call_result.is_error
        assert "required argument" in tool_call_result.content

    def test_file_tool_failure_returns_error_result(
        self, agent_loop: AgentLoop
    ) -> None:
        tool_call_result = agent_loop.tool_call_dispatch(
            "file_read", {"relative_path": "absent.txt"}
        )
        assert tool_call_result.is_error
        assert "absent.txt" in tool_call_result.content

    def test_file_write_then_read_through_dispatch(
        self, agent_loop: AgentLoop
    ) -> None:
        write_result = agent_loop.tool_call_dispatch(
            "file_write", {"relative_path": "built.txt", "content": "data"}
        )
        assert not write_result.is_error
        read_result = agent_loop.tool_call_dispatch(
            "file_read", {"relative_path": "built.txt"}
        )
        assert read_result.content == "data"

    def test_file_edit_through_dispatch(self, agent_loop: AgentLoop) -> None:
        agent_loop.tool_call_dispatch(
            "file_write", {"relative_path": "app.py", "content": "count = 1\n"}
        )
        edit_result = agent_loop.tool_call_dispatch(
            "file_edit",
            {
                "relative_path": "app.py",
                "old_text": "count = 1",
                "new_text": "count = 2",
            },
        )
        assert not edit_result.is_error
        read_result = agent_loop.tool_call_dispatch(
            "file_read", {"relative_path": "app.py"}
        )
        assert read_result.content == "count = 2\n"

    def test_file_edit_failure_returns_error_result(
        self, agent_loop: AgentLoop
    ) -> None:
        agent_loop.tool_call_dispatch(
            "file_write", {"relative_path": "app.py", "content": "x = 1\nx = 1\n"}
        )
        edit_result = agent_loop.tool_call_dispatch(
            "file_edit",
            {"relative_path": "app.py", "old_text": "x = 1", "new_text": "x = 2"},
        )
        assert edit_result.is_error
        assert "exactly once" in edit_result.content

    def test_task_complete_records_summary(self, agent_loop: AgentLoop) -> None:
        tool_call_result = agent_loop.tool_call_dispatch(
            "task_complete", {"summary": "All goals met."}
        )
        assert not tool_call_result.is_error
        assert agent_loop.completion_summary == "All goals met."

    def test_system_prompt_contains_every_foundation_document(
        self, agent_loop: AgentLoop
    ) -> None:
        for file_name in FOUNDATION_DOCUMENT_FILE_NAMES:
            assert file_name in agent_loop.system_prompt


class TestModelConfiguration:
    def test_defaults_to_anthropic_cloud_with_caching(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("NOETIC_MODEL_ID", raising=False)
        monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
        model_configuration = model_configuration_load()
        assert model_configuration.model_id == DEFAULT_MODEL_ID
        assert model_configuration.base_url is None
        assert model_configuration.prompt_caching_is_enabled

    def test_model_id_override_is_respected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NOETIC_MODEL_ID", "qwen3-coder")
        model_configuration = model_configuration_load()
        assert model_configuration.model_id == "qwen3-coder"

    def test_custom_base_url_disables_prompt_caching(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://192.168.1.10:11434")
        model_configuration = model_configuration_load()
        assert model_configuration.base_url == "http://192.168.1.10:11434"
        assert not model_configuration.prompt_caching_is_enabled

    def test_system_prompt_block_carries_cache_control_when_enabled(self) -> None:
        system_prompt_block = system_prompt_block_build("prompt", True)
        assert system_prompt_block["cache_control"] == {"type": "ephemeral"}

    def test_system_prompt_block_omits_cache_control_when_disabled(self) -> None:
        system_prompt_block = system_prompt_block_build("prompt", False)
        assert "cache_control" not in system_prompt_block


class TestShellApproval:
    def test_default_policy_is_allow(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("NOETIC_SHELL_APPROVAL", raising=False)
        model_configuration = model_configuration_load()
        assert model_configuration.shell_approval_policy == SHELL_APPROVAL_ALLOW

    def test_ask_policy_is_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NOETIC_SHELL_APPROVAL", SHELL_APPROVAL_ASK)
        model_configuration = model_configuration_load()
        assert model_configuration.shell_approval_policy == SHELL_APPROVAL_ASK

    def test_invalid_policy_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NOETIC_SHELL_APPROVAL", "sometimes")
        with pytest.raises(ConfigurationError) as error_info:
            model_configuration_load()
        assert "sometimes" in str(error_info.value)

    def test_declined_command_returns_error_result(
        self,
        supervised_agent_loop: AgentLoop,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("builtins.input", lambda prompt: "n")
        tool_call_result = supervised_agent_loop.tool_call_dispatch(
            "shell_command_run", {"command": "echo hello"}
        )
        assert tool_call_result.is_error
        assert "declined" in tool_call_result.content

    def test_approved_command_runs(
        self,
        supervised_agent_loop: AgentLoop,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("builtins.input", lambda prompt: "y")
        tool_call_result = supervised_agent_loop.tool_call_dispatch(
            "shell_command_run", {"command": "echo hello"}
        )
        assert not tool_call_result.is_error
        assert "hello" in tool_call_result.content

    def test_allow_policy_never_prompts(
        self, agent_loop: AgentLoop, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def input_forbidden(prompt: str) -> str:
            raise AssertionError("input() must not be called under allow policy")

        monkeypatch.setattr("builtins.input", input_forbidden)
        tool_call_result = agent_loop.tool_call_dispatch(
            "shell_command_run", {"command": "echo hello"}
        )
        assert not tool_call_result.is_error


class TestEnvironmentValidation:
    def test_missing_api_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(ConfigurationError):
            api_key_verify()

    def test_present_api_key_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        api_key_verify()

    def test_missing_documents_are_named(self, file_store: FileStore) -> None:
        with pytest.raises(ConfigurationError) as error_info:
            foundation_documents_verify(file_store)
        for file_name in FOUNDATION_DOCUMENT_FILE_NAMES:
            assert file_name in str(error_info.value)
