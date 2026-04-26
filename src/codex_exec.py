# -*- coding: utf-8 -*-
"""Codex CLI based LLM runtime.

This module lets the app use a locally authenticated Codex CLI session as an
LLM provider without storing API tokens in the project configuration.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

CODEX_PROVIDER = "codex"
DEFAULT_CODEX_EXEC_COMMAND = "codex"
DEFAULT_CODEX_EXEC_ARGS = (
    "--dangerously-bypass-approvals-and-sandbox "
    "--ignore-user-config --ignore-rules --skip-git-repo-check --ephemeral "
    "--disable plugins --disable apps --disable browser_use --disable computer_use "
    "--disable in_app_browser --disable shell_tool --disable tool_search "
    "--disable web_search_cached --disable web_search_request --disable general_analytics "
    "-c 'model_reasoning_effort=\"low\"' -c 'support_websocket=false'"
)
DEFAULT_CODEX_AGENT_ARGS = "--dangerously-bypass-approvals-and-sandbox"
DEFAULT_CODEX_EXEC_TIMEOUT_SECONDS = 180
DEFAULT_CODEX_AGENT_TIMEOUT_SECONDS = 600
DEFAULT_CODEX_AGENT_BACKGROUND_TIMEOUT_SECONDS = 7200

_COMPLETION_GUARD = """IMPORTANT:
You are being used as a plain LLM completion backend for another application, not as a coding agent.
Do not use web search, shell commands, files, browser tools, plugins, MCP tools, or any external data source.
Do not inspect or modify the repository. Do not load project rules or skills.
Answer only from the messages below. If required data is missing, say it is missing instead of looking it up.
Return only the requested final answer."""


class CodexExecError(RuntimeError):
    """Raised when the Codex CLI runtime cannot complete a request."""


def is_codex_exec_model(model: str) -> bool:
    """Return True for model identifiers routed through Codex CLI."""
    if not model or "/" not in model:
        return False
    provider = model.split("/", 1)[0].strip().lower().replace("-", "_")
    return provider in {CODEX_PROVIDER, "codex_cli", "codex_exec"}


def normalize_codex_exec_model(model: str) -> str:
    """Normalize Codex provider aliases to ``codex/<model>``."""
    if not is_codex_exec_model(model):
        return model
    _, raw_model = model.split("/", 1)
    return f"{CODEX_PROVIDER}/{raw_model.strip()}"


def strip_codex_exec_provider(model: str) -> str:
    """Return the CLI model name without the ``codex/`` provider prefix."""
    normalized = normalize_codex_exec_model(model)
    if normalized.startswith(f"{CODEX_PROVIDER}/"):
        return normalized.split("/", 1)[1].strip()
    return normalized.strip()


def filter_codex_exec_model_list(model_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Remove Codex CLI entries from a LiteLLM Router model list."""
    filtered: List[Dict[str, Any]] = []
    for entry in model_list or []:
        params = entry.get("litellm_params", {}) or {}
        model_name = str(params.get("model") or entry.get("model_name") or "").strip()
        if is_codex_exec_model(model_name):
            continue
        filtered.append(entry)
    return filtered


def _content_to_text(content: Any) -> str:
    """Render message content into text suitable for a Codex CLI prompt."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False)


class CodexExecClient:
    """Small subprocess wrapper around ``codex exec``."""

    def __init__(self, config: Optional[Any] = None):
        self._config = config

    def _config_value(self, name: str, default: Any) -> Any:
        return getattr(self._config, name, default) if self._config is not None else default

    def _resolve_model(self, runtime_model: str) -> str:
        model = strip_codex_exec_provider(runtime_model or "")
        if not model:
            model = str(self._config_value("codex_exec_model", "") or "").strip()
        if not model:
            raise CodexExecError("Codex exec model is not configured")
        return model

    def complete_messages(
        self,
        messages: List[Dict[str, Any]],
        *,
        model: str,
        tools: Optional[List[dict]] = None,
        timeout: Optional[float] = None,
    ) -> str:
        """Complete a chat-style message list through Codex CLI."""
        prompt = self._build_prompt(messages, tools=tools)
        return self.complete_prompt(prompt, model=model, timeout=timeout)

    def complete_prompt(
        self,
        prompt: str,
        *,
        model: str,
        timeout: Optional[float] = None,
    ) -> str:
        """Run ``codex exec`` as an isolated completion backend."""
        extra_args = str(
            self._config_value("codex_exec_args", DEFAULT_CODEX_EXEC_ARGS)
            if self._config_value("codex_exec_args", DEFAULT_CODEX_EXEC_ARGS) is not None
            else DEFAULT_CODEX_EXEC_ARGS
        )
        return self._run_exec(prompt, model=model, extra_args=extra_args, timeout=timeout)

    def complete_agent_prompt(
        self,
        prompt: str,
        *,
        model: str,
        timeout: Optional[float] = None,
    ) -> str:
        """Run ``codex exec`` in agent mode and return its final assistant message.

        This intentionally does not use the guarded completion prompt or the
        default isolated argument set.  It is used when the user explicitly
        selects a local Codex skill and expects Codex to run that skill with
        its normal local capabilities.
        """
        extra_args = str(
            self._config_value("codex_exec_agent_args", DEFAULT_CODEX_AGENT_ARGS)
            if self._config_value("codex_exec_agent_args", DEFAULT_CODEX_AGENT_ARGS) is not None
            else DEFAULT_CODEX_AGENT_ARGS
        )
        return self._run_exec(prompt, model=model, extra_args=extra_args, timeout=timeout)

    def _run_exec(
        self,
        prompt: str,
        *,
        model: str,
        extra_args: str,
        timeout: Optional[float],
    ) -> str:
        """Run ``codex exec`` and return its final assistant message."""
        cli_model = self._resolve_model(model)
        command = str(
            self._config_value("codex_exec_command", DEFAULT_CODEX_EXEC_COMMAND)
            or DEFAULT_CODEX_EXEC_COMMAND
        )
        timeout_seconds = timeout
        if timeout_seconds is None or timeout_seconds <= 0:
            timeout_seconds = float(
                self._config_value("codex_exec_timeout_seconds", DEFAULT_CODEX_EXEC_TIMEOUT_SECONDS)
                or DEFAULT_CODEX_EXEC_TIMEOUT_SECONDS
            )

        command_parts = shlex.split(command)
        if not command_parts:
            command_parts = [DEFAULT_CODEX_EXEC_COMMAND]
        if command_parts[-1] != "exec":
            command_parts.append("exec")

        arg_parts = shlex.split(extra_args)
        with tempfile.NamedTemporaryFile(prefix="dsa-codex-", suffix=".txt", delete=False) as tmp_file:
            output_path = Path(tmp_file.name)

        cmd = command_parts + arg_parts
        if "-m" not in cmd and "--model" not in cmd:
            cmd.extend(["-m", cli_model])
        if "-o" not in cmd and "--output-last-message" not in cmd:
            cmd.extend(["--output-last-message", str(output_path)])
        if "-" not in cmd:
            cmd.append("-")

        try:
            result = subprocess.run(
                cmd,
                input=prompt,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=timeout_seconds,
                cwd=os.getcwd(),
                check=False,
            )
        except FileNotFoundError as exc:
            raise CodexExecError(
                f"Codex CLI command not found: {command_parts[0]}. "
                "Install Codex CLI or set CODEX_EXEC_COMMAND."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise CodexExecError(f"Codex exec timed out after {timeout_seconds:.0f}s") from exc
        finally:
            output_text = ""
            try:
                if output_path.exists():
                    output_text = output_path.read_text(encoding="utf-8").strip()
            finally:
                try:
                    output_path.unlink(missing_ok=True)
                except OSError:
                    pass

        stdout_text = (result.stdout or "").strip()
        stderr_text = (result.stderr or "").strip()
        if result.returncode != 0:
            detail = stderr_text or stdout_text or f"exit code {result.returncode}"
            raise CodexExecError(f"Codex exec failed: {detail}")

        final_text = output_text or stdout_text
        if not final_text:
            raise CodexExecError("Codex exec returned an empty response")
        return final_text

    def _build_prompt(self, messages: List[Dict[str, Any]], *, tools: Optional[List[dict]]) -> str:
        sections: List[str] = [_COMPLETION_GUARD]
        if tools:
            sections.append(
                "You are acting as a chat-completion model for an application. "
                "When a tool is needed, do not execute it yourself. Return only JSON using this shape: "
                '{"content": null, "tool_calls": [{"id": "call_1", "name": "tool_name", "arguments": {}}]}. '
                "When no tool is needed, return only JSON using this shape: "
                '{"content": "final answer", "tool_calls": []}. '
                "Do not wrap the JSON in Markdown."
            )
            sections.append("Available tools JSON:\n" + json.dumps(tools, ensure_ascii=False))

        for message in messages:
            role = str(message.get("role") or "user").upper()
            content_text = _content_to_text(message.get("content"))
            if message.get("tool_calls"):
                tool_call_text = json.dumps(message.get("tool_calls"), ensure_ascii=False)
                content_text = f"{content_text}\nTool calls requested:\n{tool_call_text}".strip()
            if role == "TOOL":
                tool_name = message.get("name") or message.get("tool_call_id") or "tool"
                sections.append(f"{role} ({tool_name}):\n{content_text}")
            else:
                sections.append(f"{role}:\n{content_text}")

        return "\n\n".join(section for section in sections if section)
