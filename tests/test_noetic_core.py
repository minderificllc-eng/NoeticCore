"""Unit tests for the non-API parts of noetic_core.

The Anthropic client is never contacted: AgentLoop tests inject a placeholder
client object and exercise only construction and tool dispatch.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from noetic_core import (
    FOUNDATION_DOCUMENT_FILE_NAMES,
    MAXIMUM_TOOL_RESULT_CHARACTERS,
    AgentLoop,
    ConfigurationError,
    FileMissingError,
    FileStore,
    PathOutsideWorkspaceError,
    ShellCommandResult,
    ShellExecutor,
    api_key_verify,
    foundation_documents_verify,
    tool_result_content_truncate,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def file_store(workspace: Path) -> FileStore:
    return FileStore(workspace)


@pytest.fixture
def agent_loop(workspace: Path, file_store: FileStore) -> AgentLoop:
    for file_name in FOUNDATION_DOCUMENT_FILE_NAMES:
        (workspace / file_name).write_text(f"# {file_name}\n", encoding="utf-8")
    placeholder_client = object()
    return AgentLoop(placeholder_client, file_store, ShellExecutor(workspace))


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
