"""Local Claude CLI `LLMClient` -- content-side generation on the user's Claude Max subscription
via `claude -p`, at **$0 API cost** (M5, LOCAL-ONLY).

`LocalClaudeCliClient` implements the same `content.generate.LLMClient` protocol as
`AnthropicLLMClient` / `PortkeyLLMClient` -- `complete(*, system, prompt, schema=None) ->
dict[str, Any]` returning the identical structured dict -- so `generate_draft` (and the seeding /
competitor-suggestion / claim / voice call sites routed through the gateway) cannot tell the
backends apart. Selected via `Settings.llm_gateway == "local_claude"` in `content.gateway`.

Invocation recipe (verified against `claude` v2.1.201):

    claude -p --model <m> --output-format json --system-prompt <sys> \\
        --tools "" --no-session-persistence [--json-schema <json>]

with the **prompt on stdin**. Key points:

* stdout is a single JSON *envelope* (`{"result", "is_error", "subtype", "session_id", ...}`); the
  model's text is `.result`. The structured payload is then `json.loads(.result)`, with a greedy
  ``{...}`` brace-extract fallback for when the model wraps the object in prose / a code fence.
* Subscription auth: run with `CLAUDE_CONFIG_DIR` pointed at the Claude Max profile **and**
  `ANTHROPIC_API_KEY` removed from the child env (its presence would force API-key billing). We
  never pass `--bare` for the same reason.
* `--tools ""` disables all tools, so no permission prompt can ever block the headless call.
* The CLI has **no built-in timeout**: `subprocess.run(timeout=...)` enforces one and SIGKILLs the
  child on expiry.
* A neutral `cwd` (the system temp dir) keeps a stray project `CLAUDE.md` from being auto-loaded.

Hermetic: the subprocess call is an injected `runner` seam, so the test suite patches it and no
live `claude` process is ever spawned (mirrors the injected-transport convention used by the
Portkey / Anthropic clients). `complete` raises `RuntimeError` on a non-zero exit, an `is_error`
envelope, a timeout, or an unparseable result.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from typing import Any

from gw_geo.content.generate import _RESPONSE_SCHEMA

# Greedy structured-object match, used as a lenient fallback when `.result` isn't already clean
# JSON (e.g. the model prefaced it with prose or wrapped it in a ```json fence).
_BRACE_RE = re.compile(r"\{[\s\S]*\}")

# The subprocess seam -- (argv, stdin_bytes, env, cwd, timeout_s) -> CompletedProcess. Injectable
# so the hermetic suite patches it and never spawns a real `claude`; the default is `_run_claude`.
CliRunner = Callable[
    [list[str], bytes, "Mapping[str, str]", str, float],
    "subprocess.CompletedProcess[bytes]",
]


def _run_claude(
    argv: list[str], stdin: bytes, env: Mapping[str, str], cwd: str, timeout: float
) -> subprocess.CompletedProcess[bytes]:
    """Default `CliRunner`: spawn `argv` via `subprocess.run`, capturing stdout/stderr as bytes.

    `timeout` is enforced here (the CLI has none): `subprocess.run` SIGKILLs the child and raises
    `TimeoutExpired` on expiry, which `LocalClaudeCliClient.complete` maps to a `RuntimeError`.
    Never exercised by the test suite -- tests inject a fake `runner`.
    """
    return subprocess.run(
        argv,
        input=stdin,
        env=dict(env),
        cwd=cwd,
        timeout=timeout,
        capture_output=True,
    )


class LocalClaudeCliClient:
    """`LLMClient` backed by the local `claude -p` CLI on the user's Claude Max subscription ($0).

    Same `complete` contract as `AnthropicLLMClient` / `PortkeyLLMClient`: returns the structured
    dict the effective schema describes. When `schema is None` it defaults to generation's
    `_RESPONSE_SCHEMA` (so `generate_draft` can't tell the backends apart); the effective schema is
    passed to the CLI via `--json-schema`.
    """

    def __init__(
        self,
        *,
        bin: str = "claude",
        model: str = "sonnet",
        config_dir: str = "~/.asterisk/Work",
        timeout: float = 300.0,
        runner: CliRunner | None = None,
    ) -> None:
        self._bin = bin
        self._model = model
        self._config_dir = config_dir
        self._timeout = timeout
        self._runner: CliRunner = runner if runner is not None else _run_claude

    def complete(
        self, *, system: str, prompt: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        effective_schema = schema if schema is not None else _RESPONSE_SCHEMA
        argv = self._build_argv(system=system, schema=effective_schema)
        try:
            completed = self._runner(
                argv, prompt.encode(), self._child_env(), tempfile.gettempdir(), self._timeout
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"claude CLI timed out after {self._timeout}s (killed)."
            ) from exc

        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", "replace").strip()
            raise RuntimeError(f"claude CLI exited {completed.returncode}: {stderr}")

        stdout = completed.stdout.decode("utf-8", "replace")
        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"claude CLI stdout was not JSON: {stdout[:200]!r}") from exc
        if not isinstance(envelope, dict):
            raise RuntimeError(f"claude CLI envelope was not an object: {stdout[:200]!r}")
        if envelope.get("is_error"):
            raise RuntimeError(
                f"claude CLI reported is_error (subtype={envelope.get('subtype')!r}): "
                f"{envelope.get('result')!r}"
            )

        result_text = envelope.get("result")
        if not isinstance(result_text, str):
            raise RuntimeError("claude CLI envelope had no string 'result'.")
        return self._parse_structured(result_text)

    def _build_argv(self, *, system: str, schema: dict[str, Any]) -> list[str]:
        return [
            self._bin,
            "-p",
            "--model",
            self._model,
            "--output-format",
            "json",
            "--system-prompt",
            system,
            "--tools",
            "",
            "--no-session-persistence",
            "--json-schema",
            json.dumps(schema),
        ]

    def _child_env(self) -> dict[str, str]:
        """The child env: inherit `os.environ`, point `CLAUDE_CONFIG_DIR` at the Max profile, and
        drop `ANTHROPIC_API_KEY` (its presence would force API-key billing instead of the
        subscription)."""
        env = dict(os.environ)
        env["CLAUDE_CONFIG_DIR"] = os.path.expanduser(self._config_dir)
        env.pop("ANTHROPIC_API_KEY", None)
        return env

    @staticmethod
    def _parse_structured(result_text: str) -> dict[str, Any]:
        """`json.loads` the model's `.result` text into the structured dict, with a greedy
        ``{...}`` brace-extract fallback; raise `RuntimeError` if no JSON object can be recovered."""
        parsed = _loads_object(result_text)
        if parsed is None:
            match = _BRACE_RE.search(result_text)
            if match is not None:
                parsed = _loads_object(match.group(0))
        if parsed is None:
            raise RuntimeError(f"claude CLI result was not a JSON object: {result_text[:200]!r}")
        return parsed


def _loads_object(text: str) -> dict[str, Any] | None:
    """`json.loads(text)` if it yields a dict, else `None` (invalid JSON or non-object)."""
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


__all__ = ["CliRunner", "LocalClaudeCliClient"]
