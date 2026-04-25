# -*- coding: utf-8 -*-
"""Tests for Codex CLI based LLM runtime."""

import os
import sys
import unittest
from types import ModuleType
from unittest.mock import patch

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()
if "json_repair" not in sys.modules:
    json_repair_stub = ModuleType("json_repair")
    json_repair_stub.repair_json = lambda text, return_objects=False: {} if return_objects else text
    sys.modules["json_repair"] = json_repair_stub

from src.agent.llm_adapter import LLMToolAdapter
from src.config import Config
from src.codex_exec import CodexExecClient


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


if __name__ == "__main__":
    unittest.main()
