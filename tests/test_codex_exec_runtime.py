# -*- coding: utf-8 -*-
"""Tests for Codex CLI based LLM runtime."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()
if "json_repair" not in sys.modules:
    json_repair_stub = ModuleType("json_repair")
    json_repair_stub.repair_json = lambda text, return_objects=False: {} if return_objects else text
    sys.modules["json_repair"] = json_repair_stub

from src.agent.llm_adapter import LLMToolAdapter
from src.agent.executor import AgentExecutor
from src.agent.tools.registry import ToolDefinition, ToolParameter, ToolRegistry
from src.config import Config
from src.codex_exec import CodexExecClient
from src.services.codex_skill_service import list_codex_skills, load_codex_skill_instructions


class CodexExecRuntimeTestCase(unittest.TestCase):
    def tearDown(self):
        Config.reset_instance()

    @patch("src.config.setup_env")
    @patch.object(Config, "_parse_litellm_yaml", return_value=[])
    @patch.object(Config, "_parse_stock_email_groups", return_value=[])
    def test_codex_exec_enabled_infers_primary_model_without_api_key(
        self,
        _mock_stock_email_groups,
        _mock_parse_yaml,
        _mock_setup_env,
    ) -> None:
        env = {
            "CODEX_EXEC_ENABLED": "true",
            "CODEX_EXEC_MODEL": "gpt-5.4",
            "STOCK_LIST": "600519",
        }

        with patch.dict(os.environ, env, clear=True):
            config = Config._load_from_env()

        self.assertEqual(config.litellm_model, "codex/gpt-5.4")
        self.assertEqual(config.codex_exec_model, "gpt-5.4")
        self.assertEqual(config.codex_exec_agent_timeout_seconds, 600)
        self.assertEqual(config.llm_model_list, [])
        self.assertFalse(any(issue.field == "LITELLM_CONFIG" for issue in config.validate_structured()))

    @patch("src.config.setup_env")
    @patch.object(Config, "_parse_litellm_yaml", return_value=[])
    @patch.object(Config, "_parse_stock_email_groups", return_value=[])
    def test_codex_channel_accepts_empty_api_key(
        self,
        _mock_stock_email_groups,
        _mock_parse_yaml,
        _mock_setup_env,
    ) -> None:
        env = {
            "LLM_CHANNELS": "codex",
            "LLM_CODEX_PROTOCOL": "codex",
            "LLM_CODEX_MODELS": "gpt-5.4",
            "STOCK_LIST": "600519",
        }

        with patch.dict(os.environ, env, clear=True):
            config = Config._load_from_env()

        self.assertEqual(config.llm_models_source, "llm_channels")
        self.assertEqual(config.litellm_model, "codex/gpt-5.4")
        self.assertEqual(config.llm_channels[0]["api_keys"], [""])
        self.assertEqual(config.llm_model_list[0]["litellm_params"]["model"], "codex/gpt-5.4")
        self.assertNotIn("api_key", config.llm_model_list[0]["litellm_params"])

    def test_analyzer_dispatches_codex_model_to_codex_exec(self) -> None:
        try:
            from src.analyzer import GeminiAnalyzer
        except ModuleNotFoundError as exc:
            self.skipTest(f"Analyzer dependencies are not installed: {exc}")

        config = Config(
            stock_list=["600519"],
            litellm_model="codex/gpt-5.4",
            codex_exec_model="gpt-5.4",
            llm_model_list=[],
        )
        analyzer = GeminiAnalyzer(config=config)

        with patch.object(CodexExecClient, "complete_messages", return_value="ok") as mock_complete:
            text, model_used, usage = analyzer._call_litellm(
                "hello",
                generation_config={"max_tokens": 16, "temperature": 0},
            )

        self.assertEqual(text, "ok")
        self.assertEqual(model_used, "codex/gpt-5.4")
        self.assertEqual(usage, {})
        mock_complete.assert_called_once()

    def test_analyzer_does_not_retry_codex_stream_as_second_exec(self) -> None:
        try:
            from src.analyzer import GeminiAnalyzer
        except ModuleNotFoundError as exc:
            self.skipTest(f"Analyzer dependencies are not installed: {exc}")

        config = Config(
            stock_list=["600519"],
            litellm_model="codex/gpt-5.4",
            codex_exec_model="gpt-5.4",
            llm_model_list=[],
        )
        analyzer = GeminiAnalyzer(config=config)

        with patch.object(CodexExecClient, "complete_messages", side_effect=RuntimeError("boom")) as mock_complete:
            with self.assertRaises(Exception):
                analyzer._call_litellm(
                    "hello",
                    generation_config={"max_tokens": 16, "temperature": 0},
                    stream=True,
                )

        self.assertEqual(mock_complete.call_count, 1)

    def test_codex_prompt_disables_agentic_tool_use(self) -> None:
        prompt = CodexExecClient()._build_prompt(
            [{"role": "user", "content": "hello"}],
            tools=None,
        )

        self.assertIn("plain LLM completion backend", prompt)
        self.assertIn("Do not use web search", prompt)
        self.assertIn("USER:\nhello", prompt)

    def test_llm_tool_adapter_parses_codex_tool_call_protocol(self) -> None:
        config = Config(
            stock_list=["600519"],
            litellm_model="codex/gpt-5.4",
            codex_exec_model="gpt-5.4",
            llm_model_list=[],
        )
        adapter = LLMToolAdapter(config)
        raw = (
            '{"content": null, "tool_calls": ['
            '{"id": "call_1", "name": "get_quote", "arguments": {"code": "600519"}}'
            "]}"
        )

        with patch.object(CodexExecClient, "complete_messages", return_value=raw):
            response = adapter.call_with_tools(
                [{"role": "user", "content": "查一下 600519"}],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "get_quote",
                            "parameters": {"type": "object"},
                        },
                    }
                ],
            )

        self.assertIsNone(response.content)
        self.assertEqual(len(response.tool_calls), 1)
        self.assertEqual(response.tool_calls[0].name, "get_quote")
        self.assertEqual(response.tool_calls[0].arguments, {"code": "600519"})
        self.assertEqual(response.provider, "codex")

    def test_agent_chat_uses_single_codex_direct_completion(self) -> None:
        config = Config(
            stock_list=["600519"],
            litellm_model="codex/gpt-5.4",
            codex_exec_model="gpt-5.4",
            codex_exec_timeout_seconds=30,
            codex_exec_agent_timeout_seconds=45,
            llm_model_list=[],
        )
        adapter = LLMToolAdapter(config)
        registry = ToolRegistry()

        def register(name, handler):
            registry.register(
                ToolDefinition(
                    name=name,
                    description=name,
                    parameters=[
                        ToolParameter(name="stock_code", type="string", description="stock code"),
                    ],
                    handler=handler,
                )
            )

        register("get_realtime_quote", lambda stock_code: {"code": stock_code, "price": 10.0})
        register("get_daily_history", lambda stock_code, days=90: {"code": stock_code, "data": []})
        register("analyze_trend", lambda stock_code: {"code": stock_code, "signal_score": 66})
        register("get_chip_distribution", lambda stock_code: {"code": stock_code, "profit_ratio": 72})
        registry.register(
            ToolDefinition(
                name="search_stock_news",
                description="search_stock_news",
                parameters=[
                    ToolParameter(name="stock_code", type="string", description="stock code"),
                    ToolParameter(name="stock_name", type="string", description="stock name"),
                ],
                handler=lambda stock_code, stock_name: {"success": True, "results": []},
            )
        )
        executor = AgentExecutor(
            tool_registry=registry,
            llm_adapter=adapter,
            skill_instructions="默认 bull_trend 交易技能",
            timeout_seconds=60,
        )

        with patch.object(CodexExecClient, "complete_messages", return_value="Codex 问股回复") as mock_complete:
            result = executor.chat("看一下 600519", session_id="test-codex-direct-chat")

        self.assertTrue(result.success)
        self.assertEqual(result.content, "Codex 问股回复")
        self.assertEqual(result.provider, "codex")
        self.assertEqual(mock_complete.call_count, 1)
        self.assertIsNone(mock_complete.call_args.kwargs.get("tools"))
        self.assertAlmostEqual(mock_complete.call_args.kwargs.get("timeout"), 45, places=2)

    def test_codex_direct_progress_duration_and_optional_chip_miss(self) -> None:
        config = Config(
            stock_list=["600519"],
            litellm_model="codex/gpt-5.4",
            codex_exec_model="gpt-5.4",
            codex_exec_timeout_seconds=30,
            codex_exec_agent_timeout_seconds=45,
            llm_model_list=[],
        )
        adapter = LLMToolAdapter(config)
        registry = ToolRegistry()

        def register(name, handler):
            registry.register(
                ToolDefinition(
                    name=name,
                    description=name,
                    parameters=[
                        ToolParameter(name="stock_code", type="string", description="stock code"),
                    ],
                    handler=handler,
                )
            )

        register("get_realtime_quote", lambda stock_code: {"code": stock_code, "price": 10.0})
        register("get_daily_history", lambda stock_code, days=90: {"code": stock_code, "data": []})
        register("analyze_trend", lambda stock_code: {"code": stock_code, "signal_score": 66})
        register(
            "get_chip_distribution",
            lambda stock_code: {
                "code": stock_code,
                "status": "unavailable",
                "error": f"No chip distribution data available for {stock_code}",
                "retriable": False,
            },
        )
        registry.register(
            ToolDefinition(
                name="search_stock_news",
                description="search_stock_news",
                parameters=[
                    ToolParameter(name="stock_code", type="string", description="stock code"),
                    ToolParameter(name="stock_name", type="string", description="stock name"),
                ],
                handler=lambda stock_code, stock_name: {"success": True, "results": []},
            )
        )
        executor = AgentExecutor(
            tool_registry=registry,
            llm_adapter=adapter,
            skill_instructions="默认 bull_trend 交易技能",
            timeout_seconds=60,
        )

        events = []
        with patch.object(CodexExecClient, "complete_messages", return_value="Codex 问股回复"):
            result = executor.chat(
                "看一下 600519",
                session_id="test-codex-direct-progress",
                progress_callback=events.append,
            )

        self.assertTrue(result.success)
        done_events = [event for event in events if event.get("type") == "tool_done"]
        self.assertEqual(len(done_events), 5)
        self.assertTrue(all("duration" in event for event in done_events))
        chip_event = next(event for event in done_events if event.get("tool") == "get_chip_distribution")
        self.assertTrue(chip_event["success"])
        self.assertIn("暂无可用数据", chip_event["message"])
        chip_log = next(item for item in result.tool_calls_log if item["tool"] == "get_chip_distribution")
        self.assertTrue(chip_log["success"])
        self.assertIn("duration", chip_log)

    def test_codex_skill_service_discovers_local_skill_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "skills" / "custom" / "one-yang"
            skill_dir.mkdir(parents=True)
            skill_file = skill_dir / "SKILL.md"
            skill_file.write_text(
                "---\n"
                "name: 一阳夹三阴\n"
                "description: 按一阳夹三阴形态分析股票\n"
                "---\n\n"
                "# 一阳夹三阴\n\n"
                "用形态、量能和均线确认信号。\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"CODEX_HOME": tmpdir}):
                skills = list_codex_skills()
                target = next(skill for skill in skills if skill.relative_path == "custom/one-yang")
                loaded = load_codex_skill_instructions(target.id)

        self.assertGreaterEqual(len(skills), 1)
        self.assertEqual(target.name, "一阳夹三阴")
        self.assertEqual(target.description, "按一阳夹三阴形态分析股票")
        self.assertEqual(target.source, "user")
        self.assertEqual(target.relative_path, "custom/one-yang")
        self.assertIsNotNone(loaded)
        self.assertIn("用形态、量能和均线确认信号", loaded["content"])

    def test_agent_chat_with_custom_codex_skill_forces_codex_runtime(self) -> None:
        config = Config(
            stock_list=["600519"],
            litellm_model="openai/gpt-4o-mini",
            codex_exec_model="gpt-5.4",
            codex_exec_timeout_seconds=30,
            codex_exec_agent_timeout_seconds=45,
            llm_model_list=[],
        )
        adapter = LLMToolAdapter(config)
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="get_realtime_quote",
                description="get_realtime_quote",
                parameters=[
                    ToolParameter(name="stock_code", type="string", description="stock code"),
                ],
                handler=lambda stock_code: self.fail("DSA data tools must not run for Codex skill mode"),
            )
        )
        executor = AgentExecutor(
            tool_registry=registry,
            llm_adapter=adapter,
            skill_instructions="默认 bull_trend 交易技能",
            timeout_seconds=60,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "skills" / "custom" / "one-yang"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\n"
                "name: 一阳夹三阴\n"
                "description: 自定义形态分析\n"
                "---\n\n"
                "回答必须先检查一阳夹三阴形态。\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"CODEX_HOME": tmpdir}):
                skill_id = next(
                    skill.id
                    for skill in list_codex_skills()
                    if skill.relative_path == "custom/one-yang"
                )
                with patch.object(CodexExecClient, "complete_agent_prompt", return_value="自定义问询回复") as mock_complete:
                    result = executor.chat(
                        "分析中际旭创",
                        session_id="test-custom-codex-skill",
                        context={"codex_skill_id": skill_id},
                    )

        self.assertTrue(result.success)
        self.assertEqual(result.content, "自定义问询回复")
        self.assertEqual(result.provider, "codex")
        self.assertEqual(mock_complete.call_count, 1)
        self.assertAlmostEqual(mock_complete.call_args.kwargs.get("timeout"), 45, places=2)
        prompt = mock_complete.call_args.args[0]
        self.assertIn("像在 Codex 中直接调用 skill 一样", prompt)
        self.assertIn("不要使用 DSA 平台内置的行情/K线/技术/筹码/新闻工具", prompt)
        self.assertIn("回答必须先检查一阳夹三阴形态", prompt)
        self.assertNotIn("默认 bull_trend 交易技能", prompt)
        self.assertEqual(result.tool_calls_log, [])


if __name__ == "__main__":
    unittest.main()
