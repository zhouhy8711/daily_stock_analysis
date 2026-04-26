# -*- coding: utf-8 -*-
"""Background execution helpers for long-running Codex skill chat jobs."""

from __future__ import annotations

import logging
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)

_MAX_WORKERS = 2
_HEARTBEAT_SECONDS = 15.0
_OUTPUT_FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.md$")
_EXECUTOR = ThreadPoolExecutor(max_workers=_MAX_WORKERS, thread_name_prefix="codex-skill")


@dataclass(frozen=True)
class CodexSkillBackgroundJob:
    """Metadata returned when a Codex skill job is accepted for background work."""

    job_id: str
    filename: str
    relative_path: str
    output_path: Path
    output_url: str
    accepted_message: str


@dataclass(frozen=True)
class CodexSkillJobResult:
    """Result shape returned by the actual Codex skill runner."""

    success: bool
    content: str
    error: Optional[str] = None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def skill_output_dir() -> Path:
    """Return the repo-local directory for generated skill outputs."""
    return _repo_root() / "skill_out"


def resolve_skill_output_file(filename: str) -> Path:
    """Resolve a public skill output filename to a safe absolute path."""
    safe_name = str(filename or "").strip()
    if not _OUTPUT_FILENAME_RE.fullmatch(safe_name):
        raise ValueError("Invalid skill output filename")
    base = skill_output_dir().resolve()
    path = (base / safe_name).resolve()
    try:
        path.relative_to(base)
    except ValueError as exc:
        raise ValueError("Invalid skill output path") from exc
    return path


def start_codex_skill_background_job(
    *,
    session_id: str,
    user_message: str,
    skill_name: str,
    skill_path: str,
    run: Callable[[], CodexSkillJobResult],
) -> CodexSkillBackgroundJob:
    """Create a pending output file and execute the Codex skill in the background."""
    output_dir = skill_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    job_id = uuid.uuid4().hex[:12]
    started_at = datetime.now()
    filename = f"codex_skill_{started_at:%Y%m%d_%H%M%S}_{job_id}.md"
    output_path = output_dir / filename
    relative_path = f"skill_out/{filename}"
    output_url = f"/api/v1/agent/skill-output/{quote(filename)}"
    display_name = skill_name or "Codex skill"

    _write_output_file(
        output_path=output_path,
        status="running",
        skill_name=display_name,
        skill_path=skill_path,
        session_id=session_id,
        user_message=user_message,
        started_at=started_at,
        finished_at=None,
        heartbeat_at=started_at,
        content="后台任务正在执行中，完成后本文件会被完整结果覆盖。",
        error=None,
    )
    logger.info(
        "Codex skill background job accepted: session=%s skill=%s output=%s",
        session_id,
        display_name,
        output_path,
    )

    accepted_message = (
        f"已将 `{display_name}` 转入后台执行。\n\n"
        f"- 输出文件：[{relative_path}]({output_url})\n"
        "- 完成后会把最终结果链接追加到本次历史对话。\n"
        "- 这类联网深度分析不再占用当前页面请求等待。"
    )

    _EXECUTOR.submit(
        _execute_job,
        session_id,
        display_name,
        skill_path,
        user_message,
        started_at,
        output_path,
        relative_path,
        output_url,
        run,
    )

    return CodexSkillBackgroundJob(
        job_id=job_id,
        filename=filename,
        relative_path=relative_path,
        output_path=output_path,
        output_url=output_url,
        accepted_message=accepted_message,
    )


def _execute_job(
    session_id: str,
    skill_name: str,
    skill_path: str,
    user_message: str,
    started_at: datetime,
    output_path: Path,
    relative_path: str,
    output_url: str,
    run: Callable[[], CodexSkillJobResult],
) -> None:
    done = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_heartbeat_job,
        args=(done, output_path, skill_name, skill_path, session_id, user_message, started_at),
        name=f"codex-skill-heartbeat-{output_path.stem}",
        daemon=True,
    )
    heartbeat_thread.start()
    finished_at = datetime.now()
    try:
        result = run()
        finished_at = datetime.now()
        success = bool(result.success and result.content)
        content = result.content or ""
        error = result.error if not success else None
    except Exception as exc:
        logger.warning("Codex skill background job failed: %s", exc, exc_info=True)
        finished_at = datetime.now()
        success = False
        content = ""
        error = str(exc)

    done.set()
    heartbeat_thread.join(timeout=1.0)

    _write_output_file(
        output_path=output_path,
        status="completed" if success else "failed",
        skill_name=skill_name,
        skill_path=skill_path,
        session_id=session_id,
        user_message=user_message,
        started_at=started_at,
        finished_at=finished_at,
        heartbeat_at=finished_at,
        content=content,
        error=error,
    )
    logger.info(
        "Codex skill background job finished: session=%s skill=%s success=%s output=%s",
        session_id,
        skill_name,
        success,
        output_path,
    )

    if success:
        message = (
            f"`{skill_name}` 后台分析已完成。\n\n"
            f"完整结果已写入 [{relative_path}]({output_url})。"
        )
    else:
        message = (
            f"`{skill_name}` 后台分析失败。\n\n"
            f"错误详情已写入 [{relative_path}]({output_url})。"
        )
    try:
        from src.agent.conversation import conversation_manager

        conversation_manager.add_message(session_id, "assistant", message)
    except Exception:
        logger.warning("Failed to append Codex skill output link to conversation", exc_info=True)


def _write_output_file(
    *,
    output_path: Path,
    status: str,
    skill_name: str,
    skill_path: str,
    session_id: str,
    user_message: str,
    started_at: datetime,
    finished_at: Optional[datetime],
    heartbeat_at: Optional[datetime],
    content: str,
    error: Optional[str],
) -> None:
    elapsed_seconds = int(((finished_at or heartbeat_at or datetime.now()) - started_at).total_seconds())
    lines = [
        "# Codex Skill Output",
        "",
        f"- Status: {status}",
        f"- Skill: {skill_name}",
        f"- Skill path: {skill_path or '-'}",
        f"- Session: {session_id}",
        f"- Started at: {started_at.isoformat(timespec='seconds')}",
        f"- Elapsed seconds: {max(0, elapsed_seconds)}",
    ]
    if heartbeat_at is not None:
        lines.append(f"- Last heartbeat at: {heartbeat_at.isoformat(timespec='seconds')}")
    if finished_at is not None:
        lines.append(f"- Finished at: {finished_at.isoformat(timespec='seconds')}")
    lines.extend([
        "",
        "## User Request",
        "",
        user_message,
        "",
    ])
    if error:
        lines.extend(["## Error", "", error, ""])
    if content:
        lines.extend(["## Result", "", content, ""])
    _atomic_write_text(output_path, "\n".join(lines))


def _heartbeat_job(
    done: threading.Event,
    output_path: Path,
    skill_name: str,
    skill_path: str,
    session_id: str,
    user_message: str,
    started_at: datetime,
) -> None:
    while not done.wait(_HEARTBEAT_SECONDS):
        now = datetime.now()
        try:
            _write_output_file(
                output_path=output_path,
                status="running",
                skill_name=skill_name,
                skill_path=skill_path,
                session_id=session_id,
                user_message=user_message,
                started_at=started_at,
                finished_at=None,
                heartbeat_at=now,
                content=(
                    "后台任务仍在执行中。\n\n"
                    f"- 已运行：{int((now - started_at).total_seconds())} 秒\n"
                    "- Codex CLI agent 模式在最终回答返回前通常不会产生可展示的正文流。"
                ),
                error=None,
            )
        except Exception:
            logger.warning("Failed to write Codex skill heartbeat for %s", output_path, exc_info=True)


def _atomic_write_text(output_path: Path, content: str) -> None:
    tmp_path = output_path.with_name(f".{output_path.name}.tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(output_path)
