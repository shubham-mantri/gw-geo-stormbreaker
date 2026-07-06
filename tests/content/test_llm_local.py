"""Hermetic tests for `LocalClaudeCliClient` (the local `claude -p` subscription backend, M5).

The subprocess call is an injected `runner` seam, so **no live `claude` process is ever spawned**:
every test drives a `FakeRunner` that records the argv / stdin / env / cwd it was handed and returns
a canned `CompletedProcess` (or raises `TimeoutExpired`). These assert the invocation recipe (argv
shape, prompt-on-stdin, subscription env), the `.result`-envelope -> dict parse (incl. the greedy
brace-extract fallback and the schema default), and the `RuntimeError` failure modes.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from typing import Any

import pytest

from gw_geo.content.generate import _RESPONSE_SCHEMA
from gw_geo.content.llm_local import LocalClaudeCliClient


class FakeRunner:
    """A `CliRunner` double: records what it was called with, returns a canned result (or times out)."""

    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        raise_timeout: bool = False,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self._returncode = returncode
        self._raise_timeout = raise_timeout
        self.argv: list[str] | None = None
        self.stdin: bytes | None = None
        self.env: dict[str, str] | None = None
        self.cwd: str | None = None
        self.timeout: float | None = None

    def __call__(
        self, argv: list[str], stdin: bytes, env: Any, cwd: str, timeout: float
    ) -> subprocess.CompletedProcess[bytes]:
        self.argv, self.stdin, self.env, self.cwd, self.timeout = (
            argv, stdin, dict(env), cwd, timeout
        )
        if self._raise_timeout:
            raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout)
        return subprocess.CompletedProcess(argv, self._returncode, self._stdout, self._stderr)


def _envelope(result: str, *, is_error: bool = False, subtype: str = "success") -> bytes:
    """A `claude -p --output-format json` stdout envelope carrying `result` as the model's text."""
    return json.dumps(
        {"result": result, "is_error": is_error, "subtype": subtype, "session_id": "s1"}
    ).encode()


_GEN_PAYLOAD = {"title": "T", "body_markdown": "B", "schema_jsonld": {"@type": "FAQPage"}}


# --- happy path: parse .result -> dict -------------------------------------------------------


def test_complete_parses_result_envelope_into_dict() -> None:
    runner = FakeRunner(stdout=_envelope(json.dumps(_GEN_PAYLOAD)))
    client = LocalClaudeCliClient(model="sonnet", runner=runner)
    out = client.complete(
        system="sys", prompt="write it", schema={"type": "object", "properties": {}}
    )
    assert out == _GEN_PAYLOAD
    assert runner.stdin == b"write it"  # prompt on stdin


def test_no_schema_defaults_to_generation_response_schema() -> None:
    payload = {"title": "t", "body_markdown": "b", "schema_jsonld": {}}
    runner = FakeRunner(stdout=_envelope(json.dumps(payload)))
    out = LocalClaudeCliClient(runner=runner).complete(system="s", prompt="p")  # schema=None
    assert out == payload  # SAME structured contract as Anthropic/Portkey -- never free text
    argv = runner.argv or []
    # schema=None -> the effective schema is generation's _RESPONSE_SCHEMA (backends indistinguishable)
    assert json.loads(argv[argv.index("--json-schema") + 1]) == _RESPONSE_SCHEMA


# --- argv shape + subscription env -----------------------------------------------------------


def test_argv_shape_matches_recipe() -> None:
    runner = FakeRunner(stdout=_envelope(json.dumps({"x": 1})))
    LocalClaudeCliClient(bin="claude", model="sonnet", runner=runner).complete(
        system="my system", prompt="the prompt", schema={"type": "object"}
    )
    argv = runner.argv or []
    assert argv[0] == "claude"
    assert argv[1] == "-p"
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--system-prompt") + 1] == "my system"
    assert argv[argv.index("--tools") + 1] == ""  # tools disabled -> no permission prompts
    assert "--no-session-persistence" in argv
    assert "--bare" not in argv  # --bare would force API-key auth
    assert json.loads(argv[argv.index("--json-schema") + 1]) == {"type": "object"}
    assert runner.stdin == b"the prompt"


def test_child_env_sets_config_dir_and_removes_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-be-removed")
    monkeypatch.setenv("PATH", "/usr/bin")
    runner = FakeRunner(stdout=_envelope(json.dumps({"x": 1})))
    LocalClaudeCliClient(config_dir="~/.asterisk/Work", runner=runner).complete(
        system="s", prompt="p", schema={"type": "object"}
    )
    env = runner.env or {}
    assert "ANTHROPIC_API_KEY" not in env  # subscription auth, not API-key billing
    assert env["CLAUDE_CONFIG_DIR"] == os.path.expanduser("~/.asterisk/Work")  # Claude Max profile
    assert env.get("PATH") == "/usr/bin"  # rest of os.environ inherited


def test_runs_in_neutral_tempdir_cwd() -> None:
    runner = FakeRunner(stdout=_envelope(json.dumps({"x": 1})))
    LocalClaudeCliClient(runner=runner).complete(system="s", prompt="p", schema={"type": "object"})
    assert runner.cwd == tempfile.gettempdir()  # neutral cwd -> no stray CLAUDE.md loads


# --- parse fallbacks -------------------------------------------------------------------------


def test_brace_extract_fallback_recovers_wrapped_object() -> None:
    wrapped = 'Here is the result:\n```json\n{"title": "T", "body_markdown": "B", "schema_jsonld": {}}\n```'
    runner = FakeRunner(stdout=_envelope(wrapped))
    out = LocalClaudeCliClient(runner=runner).complete(system="s", prompt="p")
    assert out == {"title": "T", "body_markdown": "B", "schema_jsonld": {}}


# --- failure modes: all raise RuntimeError ---------------------------------------------------


def test_nonzero_exit_raises() -> None:
    runner = FakeRunner(stderr=b"boom", returncode=2)
    with pytest.raises(RuntimeError, match="exited 2"):
        LocalClaudeCliClient(runner=runner).complete(system="s", prompt="p", schema={"type": "object"})


def test_is_error_envelope_raises() -> None:
    runner = FakeRunner(stdout=_envelope("declined", is_error=True, subtype="error_max_turns"))
    with pytest.raises(RuntimeError, match="is_error"):
        LocalClaudeCliClient(runner=runner).complete(system="s", prompt="p", schema={"type": "object"})


def test_non_json_stdout_raises() -> None:
    runner = FakeRunner(stdout=b"totally not json")
    with pytest.raises(RuntimeError, match="stdout was not JSON"):
        LocalClaudeCliClient(runner=runner).complete(system="s", prompt="p", schema={"type": "object"})


def test_missing_result_field_raises() -> None:
    runner = FakeRunner(stdout=json.dumps({"is_error": False}).encode())  # no "result"
    with pytest.raises(RuntimeError, match="no string 'result'"):
        LocalClaudeCliClient(runner=runner).complete(system="s", prompt="p", schema={"type": "object"})


def test_unparseable_result_raises() -> None:
    runner = FakeRunner(stdout=_envelope("not json at all, no braces here"))
    with pytest.raises(RuntimeError, match="not a JSON object"):
        LocalClaudeCliClient(runner=runner).complete(system="s", prompt="p", schema={"type": "object"})


def test_timeout_raises() -> None:
    runner = FakeRunner(raise_timeout=True)
    with pytest.raises(RuntimeError, match="timed out"):
        LocalClaudeCliClient(timeout=1.0, runner=runner).complete(
            system="s", prompt="p", schema={"type": "object"}
        )


def test_constructible_with_default_runner() -> None:
    # No live call -- just verifies the default (real-subprocess) runner is wired in without error.
    assert isinstance(LocalClaudeCliClient(), LocalClaudeCliClient)
