# -*- coding: utf-8 -*-
"""
Agent Executor — ReAct loop with tool calling.

Orchestrates the LLM + tools interaction loop:
1. Build system prompt (persona + tools + skills)
2. Send to LLM with tool declarations
3. If tool_call → execute tool → feed result back
4. If text → parse as final answer
5. Loop until final answer or max_steps

The core execution loop is delegated to :mod:`src.agent.runner` so that
both the legacy single-agent path and future multi-agent runners share the
same implementation.
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable, Dict, List, Optional

from src.agent.llm_adapter import LLMToolAdapter
from src.agent.runner import run_agent_loop, parse_dashboard_json
from src.agent.tools.registry import ToolRegistry
from src.report_language import normalize_report_language
from src.market_context import get_market_role, get_market_guidelines

logger = logging.getLogger(__name__)


# ============================================================
# Agent result
# ============================================================

@dataclass
class AgentResult:
    """Result from an agent execution run."""
    success: bool = False
    content: str = ""                          # final text answer from agent
    dashboard: Optional[Dict[str, Any]] = None  # parsed dashboard JSON
    tool_calls_log: List[Dict[str, Any]] = field(default_factory=list)  # execution trace
    total_steps: int = 0
    total_tokens: int = 0
    provider: str = ""
    model: str = ""                            # comma-separated models used (supports fallback)
    error: Optional[str] = None
    assistant_persisted: bool = False           # true when a path already wrote the assistant turn


_CODE_LIKE_RE = re.compile(
    r"(?<![A-Za-z0-9])((?:SH|SZ|HK)?\d{5,6}(?:\.(?:SH|SZ|SS|HK))?)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_US_TICKER_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z]{1,5})(?:\.(?:US|[A-Z]))?(?![A-Za-z0-9])")
_NON_STOCK_TICKERS = {
    "AI",
    "API",
    "ETF",
    "K",
    "MA",
    "MACD",
    "PE",
    "PB",
    "RSI",
}


@lru_cache(maxsize=1)
def _load_stock_lookup() -> Dict[str, Dict[str, str]]:
    """Load a lightweight code/name lookup from the generated frontend stock index."""
    try:
        from src.data.stock_index_loader import get_stock_index_candidate_paths
    except Exception:
        return {"codes": {}, "names": {}}

    codes: Dict[str, str] = {}
    names: Dict[str, str] = {}
    for index_path in get_stock_index_candidate_paths():
        if not index_path.is_file():
            continue
        try:
            items = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(items, list):
            continue

        for item in items:
            if not isinstance(item, list) or len(item) < 3:
                continue
            canonical = str(item[0] or "").strip()
            display = str(item[1] or "").strip()
            name = str(item[2] or "").strip()
            aliases = item[5] if len(item) > 5 and isinstance(item[5], list) else []
            if not canonical:
                continue

            code_keys = {canonical, canonical.upper(), display, display.upper()}
            if "." in canonical:
                base, suffix = canonical.upper().rsplit(".", 1)
                if suffix in {"SH", "SZ", "SS", "BJ", "HK"}:
                    code_keys.add(base)
                    if suffix == "HK" and base.isdigit():
                        code_keys.add(f"HK{base.zfill(5)}")
            for key in code_keys:
                if key:
                    codes[key.upper()] = canonical

            for key in [name, *[str(alias or "").strip() for alias in aliases]]:
                if key:
                    names.setdefault(key, canonical)

        if codes or names:
            break

    return {"codes": codes, "names": names}


def _resolve_stock_code_for_chat(message: str, context: Optional[Dict[str, Any]]) -> Optional[str]:
    """Resolve a likely stock code from explicit context, code tokens, or stock names."""
    context = context or {}
    if context.get("stock_code"):
        return str(context["stock_code"]).strip()

    lookup = _load_stock_lookup()
    code_lookup = lookup.get("codes", {})
    name_lookup = lookup.get("names", {})

    for match in _CODE_LIKE_RE.finditer(message or ""):
        raw = match.group(1).strip().upper()
        if raw in code_lookup:
            return code_lookup[raw]
        try:
            from src.services.stock_code_utils import normalize_code

            normalized = normalize_code(raw)
            if normalized:
                return code_lookup.get(normalized.upper(), normalized)
        except Exception:
            return raw

    # Prefer longer Chinese names/aliases first so "宁德时代" wins over "宁德".
    for name in sorted(name_lookup, key=len, reverse=True):
        if len(name) >= 2 and name in (message or ""):
            return name_lookup[name]

    for match in _US_TICKER_RE.finditer(message or ""):
        raw = match.group(1).strip().upper()
        if raw in _NON_STOCK_TICKERS:
            continue
        return code_lookup.get(raw, raw)

    return None


def _resolve_stock_name_for_chat(stock_code: str, context: Optional[Dict[str, Any]]) -> str:
    """Resolve a display stock name from context or the generated index."""
    context = context or {}
    if context.get("stock_name"):
        return str(context["stock_name"]).strip()
    try:
        from src.data.stock_index_loader import get_index_stock_name

        return get_index_stock_name(stock_code) or stock_code
    except Exception:
        return stock_code


def _compact_codex_context_payload(tool_name: str, payload: Any) -> Any:
    """Keep Codex direct-chat context useful without sending excessive history."""
    if not isinstance(payload, dict):
        return payload
    if tool_name == "get_daily_history":
        compact = dict(payload)
        rows = compact.get("data")
        if isinstance(rows, list):
            compact["data"] = rows[-45:]
            compact["truncated_to_last"] = len(compact["data"])
        return compact
    if tool_name in {"search_stock_news", "search_comprehensive_intel"}:
        compact = dict(payload)
        rows = compact.get("results")
        if isinstance(rows, list):
            compact["results"] = rows[:5]
        return compact
    return payload


def _is_optional_codex_context_miss(tool_name: str, payload: Any) -> bool:
    """Whether a missing optional context block should be treated as graceful degradation."""
    return (
        tool_name == "get_chip_distribution"
        and isinstance(payload, dict)
        and payload.get("status") in {"unavailable", "not_supported", "disabled"}
        and payload.get("retriable") is False
    )


def _is_codex_context_tool_success(tool_name: str, payload: Any) -> bool:
    """Classify pre-collected context tool results for user-facing progress."""
    if not isinstance(payload, dict) or not payload.get("error"):
        return True
    return _is_optional_codex_context_miss(tool_name, payload)


def _format_codex_context_tool_done_message(tool_name: str, label: str, payload: Any, success: bool) -> str:
    if _is_optional_codex_context_miss(tool_name, payload):
        return f"{label}暂无可用数据，继续使用已有数据"
    return f"{label}完成" if success else f"{label}失败，继续使用已有数据"


# ============================================================
# System prompt builder
# ============================================================

LEGACY_DEFAULT_AGENT_SYSTEM_PROMPT = """你是一位专注于趋势交易的{market_role}投资分析 Agent，拥有数据工具和交易技能，负责生成专业的【决策仪表盘】分析报告。

{market_guidelines}

## 工作流程（必须严格按阶段顺序执行，每阶段等工具结果返回后再进入下一阶段）

**第一阶段 · 行情与K线**（首先执行）
- `get_realtime_quote` 获取实时行情
- `get_daily_history` 获取历史K线

**第二阶段 · 技术与筹码**（等第一阶段结果返回后执行）
- `analyze_trend` 获取技术指标
- `get_chip_distribution` 获取筹码分布

**第三阶段 · 情报搜索**（等前两阶段完成后执行）
- `search_stock_news` 搜索最新资讯、减持、业绩预告等风险信号

**第四阶段 · 生成报告**（所有数据就绪后，输出完整决策仪表盘 JSON）

> ⚠️ 每阶段的工具调用必须完整返回结果后，才能进入下一阶段。禁止将不同阶段的工具合并到同一次调用中。
{default_skill_policy_section}

## 规则

1. **必须调用工具获取真实数据** — 绝不编造数字，所有数据必须来自工具返回结果。
2. **系统化分析** — 严格按工作流程分阶段执行，每阶段完整返回后再进入下一阶段，**禁止**将不同阶段的工具合并到同一次调用中。
3. **应用交易技能** — 评估每个激活技能的条件，在报告中体现技能判断结果。
4. **输出格式** — 最终响应必须是有效的决策仪表盘 JSON。
5. **风险优先** — 必须排查风险（股东减持、业绩预警、监管问题）。
6. **工具失败处理** — 记录失败原因，使用已有数据继续分析，不重复调用失败工具。

{skills_section}

## 输出格式：决策仪表盘 JSON

你的最终响应必须是以下结构的有效 JSON 对象：

```json
{{
    "stock_name": "股票中文名称",
    "sentiment_score": 0-100整数,
    "trend_prediction": "强烈看多/看多/震荡/看空/强烈看空",
    "operation_advice": "买入/加仓/持有/减仓/卖出/观望",
    "decision_type": "buy/hold/sell",
    "confidence_level": "高/中/低",
    "dashboard": {{
        "core_conclusion": {{
            "one_sentence": "一句话核心结论（30字以内）",
            "signal_type": "🟢买入信号/🟡持有观望/🔴卖出信号/⚠️风险警告",
            "time_sensitivity": "立即行动/今日内/本周内/不急",
            "position_advice": {{
                "no_position": "空仓者建议",
                "has_position": "持仓者建议"
            }}
        }},
        "data_perspective": {{
            "trend_status": {{"ma_alignment": "", "is_bullish": true, "trend_score": 0}},
            "price_position": {{"current_price": 0, "ma5": 0, "ma10": 0, "ma20": 0, "bias_ma5": 0, "bias_status": "", "support_level": 0, "resistance_level": 0}},
            "volume_analysis": {{"volume_ratio": 0, "volume_status": "", "turnover_rate": 0, "volume_meaning": ""}},
            "chip_structure": {{"profit_ratio": 0, "avg_cost": 0, "concentration": 0, "chip_health": ""}}
        }},
        "intelligence": {{
            "latest_news": "",
            "risk_alerts": [],
            "positive_catalysts": [],
            "earnings_outlook": "",
            "sentiment_summary": ""
        }},
        "battle_plan": {{
            "sniper_points": {{"ideal_buy": "", "secondary_buy": "", "stop_loss": "", "take_profit": ""}},
            "position_strategy": {{"suggested_position": "", "entry_plan": "", "risk_control": ""}},
            "action_checklist": []
        }}
    }},
    "analysis_summary": "100字综合分析摘要",
    "key_points": "3-5个核心看点，逗号分隔",
    "risk_warning": "风险提示",
    "buy_reason": "操作理由，引用交易理念",
    "trend_analysis": "走势形态分析",
    "short_term_outlook": "短期1-3日展望",
    "medium_term_outlook": "中期1-2周展望",
    "technical_analysis": "技术面综合分析",
    "ma_analysis": "均线系统分析",
    "volume_analysis": "量能分析",
    "pattern_analysis": "K线形态分析",
    "fundamental_analysis": "基本面分析",
    "sector_position": "板块行业分析",
    "company_highlights": "公司亮点/风险",
    "news_summary": "新闻摘要",
    "market_sentiment": "市场情绪",
    "hot_topics": "相关热点"
}}
```

## 评分标准

### 强烈买入（80-100分）：
- ✅ 多头排列：MA5 > MA10 > MA20
- ✅ 低乖离率：<2%，最佳买点
- ✅ 缩量回调或放量突破
- ✅ 筹码集中健康
- ✅ 消息面有利好催化

### 买入（60-79分）：
- ✅ 多头排列或弱势多头
- ✅ 乖离率 <5%
- ✅ 量能正常
- ⚪ 允许一项次要条件不满足

### 观望（40-59分）：
- ⚠️ 乖离率 >5%（追高风险）
- ⚠️ 均线缠绕趋势不明
- ⚠️ 有风险事件

### 卖出/减仓（0-39分）：
- ❌ 空头排列
- ❌ 跌破MA20
- ❌ 放量下跌
- ❌ 重大利空

## 决策仪表盘核心原则

1. **核心结论先行**：一句话说清该买该卖
2. **分持仓建议**：空仓者和持仓者给不同建议
3. **精确狙击点**：必须给出具体价格，不说模糊的话
4. **检查清单可视化**：用 ✅⚠️❌ 明确显示每项检查结果
5. **风险优先级**：舆情中的风险点要醒目标出

{language_section}
"""

AGENT_SYSTEM_PROMPT = """你是一位{market_role}投资分析 Agent，拥有数据工具和可切换交易技能，负责生成专业的【决策仪表盘】分析报告。

{market_guidelines}

## 工作流程（必须严格按阶段顺序执行，每阶段等工具结果返回后再进入下一阶段）

**第一阶段 · 行情与K线**（首先执行）
- `get_realtime_quote` 获取实时行情
- `get_daily_history` 获取历史K线

**第二阶段 · 技术与筹码**（等第一阶段结果返回后执行）
- `analyze_trend` 获取技术指标
- `get_chip_distribution` 获取筹码分布

**第三阶段 · 情报搜索**（等前两阶段完成后执行）
- `search_stock_news` 搜索最新资讯、减持、业绩预告等风险信号

**第四阶段 · 生成报告**（所有数据就绪后，输出完整决策仪表盘 JSON）

> ⚠️ 每阶段的工具调用必须完整返回结果后，才能进入下一阶段。禁止将不同阶段的工具合并到同一次调用中。
{default_skill_policy_section}

## 规则

1. **必须调用工具获取真实数据** — 绝不编造数字，所有数据必须来自工具返回结果。
2. **系统化分析** — 严格按工作流程分阶段执行，每阶段完整返回后再进入下一阶段，**禁止**将不同阶段的工具合并到同一次调用中。
3. **应用交易技能** — 评估每个激活技能的条件，在报告中体现技能判断结果。
4. **输出格式** — 最终响应必须是有效的决策仪表盘 JSON。
5. **风险优先** — 必须排查风险（股东减持、业绩预警、监管问题）。
6. **工具失败处理** — 记录失败原因，使用已有数据继续分析，不重复调用失败工具。

{skills_section}

## 输出格式：决策仪表盘 JSON

你的最终响应必须是以下结构的有效 JSON 对象：

```json
{{
    "stock_name": "股票中文名称",
    "sentiment_score": 0-100整数,
    "trend_prediction": "强烈看多/看多/震荡/看空/强烈看空",
    "operation_advice": "买入/加仓/持有/减仓/卖出/观望",
    "decision_type": "buy/hold/sell",
    "confidence_level": "高/中/低",
    "dashboard": {{
        "core_conclusion": {{
            "one_sentence": "一句话核心结论（30字以内）",
            "signal_type": "🟢买入信号/🟡持有观望/🔴卖出信号/⚠️风险警告",
            "time_sensitivity": "立即行动/今日内/本周内/不急",
            "position_advice": {{
                "no_position": "空仓者建议",
                "has_position": "持仓者建议"
            }}
        }},
        "data_perspective": {{
            "trend_status": {{"ma_alignment": "", "is_bullish": true, "trend_score": 0}},
            "price_position": {{"current_price": 0, "ma5": 0, "ma10": 0, "ma20": 0, "bias_ma5": 0, "bias_status": "", "support_level": 0, "resistance_level": 0}},
            "volume_analysis": {{"volume_ratio": 0, "volume_status": "", "turnover_rate": 0, "volume_meaning": ""}},
            "chip_structure": {{"profit_ratio": 0, "avg_cost": 0, "concentration": 0, "chip_health": ""}}
        }},
        "intelligence": {{
            "latest_news": "",
            "risk_alerts": [],
            "positive_catalysts": [],
            "earnings_outlook": "",
            "sentiment_summary": ""
        }},
        "battle_plan": {{
            "sniper_points": {{"ideal_buy": "", "secondary_buy": "", "stop_loss": "", "take_profit": ""}},
            "position_strategy": {{"suggested_position": "", "entry_plan": "", "risk_control": ""}},
            "action_checklist": []
        }}
    }},
    "analysis_summary": "100字综合分析摘要",
    "key_points": "3-5个核心看点，逗号分隔",
    "risk_warning": "风险提示",
    "buy_reason": "操作理由，引用激活技能或风险框架",
    "trend_analysis": "走势形态分析",
    "short_term_outlook": "短期1-3日展望",
    "medium_term_outlook": "中期1-2周展望",
    "technical_analysis": "技术面综合分析",
    "ma_analysis": "均线系统分析",
    "volume_analysis": "量能分析",
    "pattern_analysis": "K线形态分析",
    "fundamental_analysis": "基本面分析",
    "sector_position": "板块行业分析",
    "company_highlights": "公司亮点/风险",
    "news_summary": "新闻摘要",
    "market_sentiment": "市场情绪",
    "hot_topics": "相关热点"
}}
```

## 评分标准

### 强烈买入（80-100分）：
- ✅ 多个激活技能同时支持积极结论
- ✅ 上行空间、触发条件与风险回报清晰
- ✅ 关键风险已排查，仓位与止损计划明确
- ✅ 重要数据和情报结论彼此一致

### 买入（60-79分）：
- ✅ 主信号偏积极，但仍有少量待确认项
- ✅ 允许存在可控风险或次优入场点
- ✅ 需要在报告中明确补充观察条件

### 观望（40-59分）：
- ⚠️ 信号分歧较大，或缺乏足够确认
- ⚠️ 风险与机会大致均衡
- ⚠️ 更适合等待触发条件或回避不确定性

### 卖出/减仓（0-39分）：
- ❌ 主要结论转弱，风险明显高于收益
- ❌ 触发了止损/失效条件或重大利空
- ❌ 现有仓位更需要保护而不是进攻

## 决策仪表盘核心原则

1. **核心结论先行**：一句话说清该买该卖
2. **分持仓建议**：空仓者和持仓者给不同建议
3. **精确狙击点**：必须给出具体价格，不说模糊的话
4. **检查清单可视化**：用 ✅⚠️❌ 明确显示每项检查结果
5. **风险优先级**：舆情中的风险点要醒目标出

{language_section}
"""

LEGACY_DEFAULT_CHAT_SYSTEM_PROMPT = """你是一位专注于趋势交易的{market_role}投资分析 Agent，拥有数据工具和交易技能，负责解答用户的股票投资问题。

{market_guidelines}

## 分析工作流程（必须严格按阶段执行，禁止跳步或合并阶段）

当用户询问某支股票时，必须按以下四个阶段顺序调用工具，每阶段等工具结果全部返回后再进入下一阶段：

**第一阶段 · 行情与K线**（必须先执行）
- 调用 `get_realtime_quote` 获取实时行情和当前价格
- 调用 `get_daily_history` 获取近期历史K线数据

**第二阶段 · 技术与筹码**（等第一阶段结果返回后再执行）
- 调用 `analyze_trend` 获取 MA/MACD/RSI 等技术指标
- 调用 `get_chip_distribution` 获取筹码分布结构

**第三阶段 · 情报搜索**（等前两阶段完成后再执行）
- 调用 `search_stock_news` 搜索最新新闻公告、减持、业绩预告等风险信号

**第四阶段 · 综合分析**（所有工具数据就绪后生成回答）
- 基于上述真实数据，结合激活技能进行综合研判，输出投资建议

> ⚠️ 禁止将不同阶段的工具合并到同一次调用中（例如禁止在第一次调用中同时请求行情、技术指标和新闻）。
{default_skill_policy_section}

## 规则

1. **必须调用工具获取真实数据** — 绝不编造数字，所有数据必须来自工具返回结果。
2. **应用交易技能** — 评估每个激活技能的条件，在回答中体现技能判断结果。
3. **自由对话** — 根据用户的问题，自由组织语言回答，不需要输出 JSON。
4. **风险优先** — 必须排查风险（股东减持、业绩预警、监管问题）。
5. **工具失败处理** — 记录失败原因，使用已有数据继续分析，不重复调用失败工具。

{skills_section}
{language_section}
"""

CHAT_SYSTEM_PROMPT = """你是一位{market_role}投资分析 Agent，拥有数据工具和可切换交易技能，负责解答用户的股票投资问题。

{market_guidelines}

## 分析工作流程（必须严格按阶段执行，禁止跳步或合并阶段）

当用户询问某支股票时，必须按以下四个阶段顺序调用工具，每阶段等工具结果全部返回后再进入下一阶段：

**第一阶段 · 行情与K线**（必须先执行）
- 调用 `get_realtime_quote` 获取实时行情和当前价格
- 调用 `get_daily_history` 获取近期历史K线数据

**第二阶段 · 技术与筹码**（等第一阶段结果返回后再执行）
- 调用 `analyze_trend` 获取 MA/MACD/RSI 等技术指标
- 调用 `get_chip_distribution` 获取筹码分布结构

**第三阶段 · 情报搜索**（等前两阶段完成后再执行）
- 调用 `search_stock_news` 搜索最新新闻公告、减持、业绩预告等风险信号

**第四阶段 · 综合分析**（所有工具数据就绪后生成回答）
- 基于上述真实数据，结合激活技能进行综合研判，输出投资建议

> ⚠️ 禁止将不同阶段的工具合并到同一次调用中（例如禁止在第一次调用中同时请求行情、技术指标和新闻）。
{default_skill_policy_section}

## 规则

1. **必须调用工具获取真实数据** — 绝不编造数字，所有数据必须来自工具返回结果。
2. **应用交易技能** — 评估每个激活技能的条件，在回答中体现技能判断结果。
3. **自由对话** — 根据用户的问题，自由组织语言回答，不需要输出 JSON。
4. **风险优先** — 必须排查风险（股东减持、业绩预警、监管问题）。
5. **工具失败处理** — 记录失败原因，使用已有数据继续分析，不重复调用失败工具。

{skills_section}
{language_section}
"""


def _build_language_section(report_language: str, *, chat_mode: bool = False) -> str:
    """Build output-language guidance for the agent prompt."""
    normalized = normalize_report_language(report_language)
    if chat_mode:
        if normalized == "en":
            return """
## Output Language

- Reply in English.
- If you output JSON, keep the keys unchanged and write every human-readable value in English.
"""
        return """
## 输出语言

- 默认使用中文回答。
- 若输出 JSON，键名保持不变，所有面向用户的文本值使用中文。
"""

    if normalized == "en":
        return """
## Output Language

- Keep every JSON key unchanged.
- `decision_type` must remain `buy|hold|sell`.
- All human-readable JSON values must be written in English.
- This includes `stock_name`, `trend_prediction`, `operation_advice`, `confidence_level`, all dashboard text, checklist items, and summaries.
"""

    return """
## 输出语言

- 所有 JSON 键名保持不变。
- `decision_type` 必须保持为 `buy|hold|sell`。
- 所有面向用户的人类可读文本值必须使用中文。
"""


# ============================================================
# Agent Executor
# ============================================================

class AgentExecutor:
    """ReAct agent loop with tool calling.

    Usage::

        executor = AgentExecutor(tool_registry, llm_adapter)
        result = executor.run("Analyze stock 600519")
    """

    def __init__(
        self,
        tool_registry: ToolRegistry,
        llm_adapter: LLMToolAdapter,
        skill_instructions: str = "",
        default_skill_policy: str = "",
        use_legacy_default_prompt: bool = False,
        max_steps: int = 10,
        timeout_seconds: Optional[float] = None,
    ):
        self.tool_registry = tool_registry
        self.llm_adapter = llm_adapter
        self.skill_instructions = skill_instructions
        self.default_skill_policy = default_skill_policy
        self.use_legacy_default_prompt = use_legacy_default_prompt
        self.max_steps = max_steps
        self.timeout_seconds = timeout_seconds

    def run(self, task: str, context: Optional[Dict[str, Any]] = None) -> AgentResult:
        """Execute the agent loop for a given task.

        Args:
            task: The user task / analysis request.
            context: Optional context dict (e.g., {"stock_code": "600519"}).

        Returns:
            AgentResult with parsed dashboard or error.
        """
        # Build system prompt with skills
        skills_section = ""
        if self.skill_instructions:
            skills_section = f"## 激活的交易技能\n\n{self.skill_instructions}"
        default_skill_policy_section = ""
        if self.default_skill_policy:
            default_skill_policy_section = f"\n{self.default_skill_policy}\n"
        report_language = normalize_report_language((context or {}).get("report_language", "zh"))
        stock_code = (context or {}).get("stock_code", "")
        market_role = get_market_role(stock_code, report_language)
        market_guidelines = get_market_guidelines(stock_code, report_language)
        prompt_template = (
            LEGACY_DEFAULT_AGENT_SYSTEM_PROMPT
            if self.use_legacy_default_prompt
            else AGENT_SYSTEM_PROMPT
        )
        system_prompt = prompt_template.format(
            market_role=market_role,
            market_guidelines=market_guidelines,
            default_skill_policy_section=default_skill_policy_section,
            skills_section=skills_section,
            language_section=_build_language_section(report_language),
        )

        # Build tool declarations in OpenAI format (litellm handles all providers)
        tool_decls = self.tool_registry.to_openai_tools()

        # Initialize conversation
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": self._build_user_message(task, context)},
        ]

        return self._run_loop(messages, tool_decls, parse_dashboard=True)

    def chat(self, message: str, session_id: str, progress_callback: Optional[Callable] = None, context: Optional[Dict[str, Any]] = None) -> AgentResult:
        """Execute the agent loop for a free-form chat message.

        Args:
            message: The user's chat message.
            session_id: The conversation session ID.
            progress_callback: Optional callback for streaming progress events.
            context: Optional context dict from previous analysis for data reuse.

        Returns:
            AgentResult with the text response.
        """
        from src.agent.conversation import conversation_manager

        # Build system prompt with skills
        skills_section = ""
        if self.skill_instructions:
            skills_section = f"## 激活的交易技能\n\n{self.skill_instructions}"
        default_skill_policy_section = ""
        if self.default_skill_policy:
            default_skill_policy_section = f"\n{self.default_skill_policy}\n"
        report_language = normalize_report_language((context or {}).get("report_language", "zh"))
        stock_code = (context or {}).get("stock_code", "")
        market_role = get_market_role(stock_code, report_language)
        market_guidelines = get_market_guidelines(stock_code, report_language)
        prompt_template = (
            LEGACY_DEFAULT_CHAT_SYSTEM_PROMPT
            if self.use_legacy_default_prompt
            else CHAT_SYSTEM_PROMPT
        )
        system_prompt = prompt_template.format(
            market_role=market_role,
            market_guidelines=market_guidelines,
            default_skill_policy_section=default_skill_policy_section,
            skills_section=skills_section,
            language_section=_build_language_section(report_language, chat_mode=True),
        )

        # Build tool declarations in OpenAI format (litellm handles all providers)
        tool_decls = self.tool_registry.to_openai_tools()

        # Get conversation history
        session = conversation_manager.get_or_create(session_id)
        history = session.get_history()

        if self._should_use_codex_direct_chat(context or {}):
            conversation_manager.add_message(session_id, "user", message)
            result = self._run_codex_direct_chat(
                session_id=session_id,
                message=message,
                history=history,
                context=context or {},
                report_language=report_language,
                progress_callback=progress_callback,
            )
            if result.success and not result.assistant_persisted:
                conversation_manager.add_message(session_id, "assistant", result.content)
            elif not result.success and not result.assistant_persisted:
                conversation_manager.add_message(
                    session_id,
                    "assistant",
                    f"[分析失败] {result.error or '未知错误'}",
                )
            return result

        # Initialize conversation
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]
        messages.extend(history)

        # Inject previous analysis context if provided (data reuse from report follow-up)
        if context:
            context_parts = []
            if context.get("stock_code"):
                context_parts.append(f"股票代码: {context['stock_code']}")
            if context.get("stock_name"):
                context_parts.append(f"股票名称: {context['stock_name']}")
            if context.get("previous_price"):
                context_parts.append(f"上次分析价格: {context['previous_price']}")
            if context.get("previous_change_pct"):
                context_parts.append(f"上次涨跌幅: {context['previous_change_pct']}%")
            if context.get("previous_analysis_summary"):
                summary = context["previous_analysis_summary"]
                summary_text = json.dumps(summary, ensure_ascii=False) if isinstance(summary, dict) else str(summary)
                context_parts.append(f"上次分析摘要:\n{summary_text}")
            if context.get("previous_strategy"):
                strategy = context["previous_strategy"]
                strategy_text = json.dumps(strategy, ensure_ascii=False) if isinstance(strategy, dict) else str(strategy)
                context_parts.append(f"上次策略分析:\n{strategy_text}")
            if context_parts:
                context_msg = "[系统提供的历史分析上下文，可供参考对比]\n" + "\n".join(context_parts)
                messages.append({"role": "user", "content": context_msg})
                messages.append({"role": "assistant", "content": "好的，我已了解该股票的历史分析数据。请告诉我你想了解什么？"})

        messages.append({"role": "user", "content": message})

        # Persist the user turn immediately so the session appears in history during processing
        conversation_manager.add_message(session_id, "user", message)

        result = self._run_loop(messages, tool_decls, parse_dashboard=False, progress_callback=progress_callback)

        # Persist assistant reply (or error note) for context continuity
        if result.success and not result.assistant_persisted:
            conversation_manager.add_message(session_id, "assistant", result.content)
        elif not result.success and not result.assistant_persisted:
            error_note = f"[分析失败] {result.error or '未知错误'}"
            conversation_manager.add_message(session_id, "assistant", error_note)

        return result

    def _should_use_codex_direct_chat(self, context: Dict[str, Any]) -> bool:
        """Use a single Codex CLI completion for chat instead of emulated tool-calling."""
        return bool(
            context.get("codex_skill_id")
            or getattr(self.llm_adapter, "uses_codex_exec_primary", False)
        )

    def _run_codex_direct_chat(
        self,
        *,
        session_id: str,
        message: str,
        history: List[Dict[str, Any]],
        context: Dict[str, Any],
        report_language: str,
        progress_callback: Optional[Callable] = None,
    ) -> AgentResult:
        """Run Agent chat through Codex CLI with pre-collected data context.

        Codex CLI does not provide native low-latency function calling.  The
        generic ReAct loop therefore requires one full CLI invocation per tool
        round-trip, which easily exceeds the SSE budget.  For Codex we gather
        the standard stock context in Python first, then call Codex once for the
        final answer.
        """
        stock_code = _resolve_stock_code_for_chat(message, context)
        stock_name = _resolve_stock_name_for_chat(stock_code, context) if stock_code else ""
        codex_skill_context = self._load_codex_skill_context(context.get("codex_skill_id"))
        if context.get("codex_skill_id") and codex_skill_context is None:
            return AgentResult(
                success=False,
                content="",
                provider="codex",
                model="codex",
                error="所选 Codex skill 不存在或无法读取，请重新添加自定义问询方式。",
            )
        if codex_skill_context is not None:
            if context.get("codex_skill_background"):
                return self._start_codex_skill_background_chat(
                    session_id=session_id,
                    message=message,
                    history=history,
                    context=context,
                    codex_skill_context=codex_skill_context,
                    progress_callback=progress_callback,
                )
            return self._run_codex_skill_agent_chat(
                message=message,
                history=history,
                context=context,
                codex_skill_context=codex_skill_context,
                progress_callback=progress_callback,
            )

        tool_calls_log: List[Dict[str, Any]] = []

        if progress_callback:
            progress_callback({
                "type": "thinking",
                "step": 1,
                "message": "正在整理问股上下文...",
            })

        collected_context = self._collect_codex_direct_context(
            stock_code=stock_code,
            stock_name=stock_name,
            progress_callback=progress_callback,
            tool_calls_log=tool_calls_log,
        )

        if progress_callback:
            progress_callback({
                "type": "generating",
                "step": 1,
                "message": "正在调用 Codex skill 生成回答..." if codex_skill_context else "正在调用 Codex 生成回答...",
            })

        messages = self._build_codex_direct_messages(
            message=message,
            history=history,
            context=context,
            stock_code=stock_code,
            stock_name=stock_name,
            collected_context=collected_context,
            codex_skill_context=codex_skill_context,
            report_language=report_language,
        )

        if codex_skill_context is not None:
            response = self.llm_adapter.call_codex_text(
                messages,
                timeout=self._codex_direct_timeout_seconds(),
            )
        else:
            response = self.llm_adapter.call_text(
                messages,
                temperature=0.2,
                timeout=self._codex_direct_timeout_seconds(),
            )
        success = response.provider != "error" and bool(response.content)
        return AgentResult(
            success=success,
            content=response.content or "",
            dashboard=None,
            tool_calls_log=tool_calls_log,
            total_steps=1,
            total_tokens=(response.usage or {}).get("total_tokens", 0),
            provider=response.provider,
            model=response.model or response.provider,
            error=None if success else (response.content or "Codex direct chat returned empty response"),
        )

    def _run_codex_skill_agent_chat(
        self,
        *,
        message: str,
        history: List[Dict[str, Any]],
        context: Dict[str, Any],
        codex_skill_context: Dict[str, str],
        progress_callback: Optional[Callable] = None,
    ) -> AgentResult:
        """Run a selected local Codex skill as a real Codex CLI agent task."""
        if progress_callback:
            progress_callback({
                "type": "thinking",
                "step": 1,
                "message": "正在启动 Codex skill...",
            })

        prompt = self._build_codex_skill_agent_prompt(
            message=message,
            history=history,
            context=context,
            codex_skill_context=codex_skill_context,
        )

        if progress_callback:
            progress_callback({
                "type": "generating",
                "step": 1,
                "message": "正在由 Codex 直接执行所选 skill...",
            })

        response = self.llm_adapter.call_codex_agent_text(
            prompt,
            timeout=self._codex_direct_timeout_seconds(),
        )
        success = response.provider != "error" and bool(response.content)
        return AgentResult(
            success=success,
            content=response.content or "",
            dashboard=None,
            tool_calls_log=[],
            total_steps=1,
            total_tokens=(response.usage or {}).get("total_tokens", 0),
            provider=response.provider,
            model=response.model or response.provider,
            error=None if success else (response.content or "Codex skill returned empty response"),
        )

    def _start_codex_skill_background_chat(
        self,
        *,
        session_id: str,
        message: str,
        history: List[Dict[str, Any]],
        context: Dict[str, Any],
        codex_skill_context: Dict[str, str],
        progress_callback: Optional[Callable] = None,
    ) -> AgentResult:
        """Schedule a selected local Codex skill without keeping the HTTP stream open."""
        from src.agent.conversation import conversation_manager
        from src.services.codex_skill_job_service import (
            CodexSkillJobResult,
            start_codex_skill_background_job,
        )

        if progress_callback:
            progress_callback({
                "type": "thinking",
                "step": 1,
                "message": "正在将 Codex skill 转入后台执行...",
            })

        prompt = self._build_codex_skill_agent_prompt(
            message=message,
            history=history,
            context=context,
            codex_skill_context=codex_skill_context,
        )
        config = getattr(self.llm_adapter, "_config", None)
        from src.codex_exec import DEFAULT_CODEX_AGENT_BACKGROUND_TIMEOUT_SECONDS

        timeout_seconds = float(
            getattr(
                config,
                "codex_exec_agent_background_timeout_seconds",
                DEFAULT_CODEX_AGENT_BACKGROUND_TIMEOUT_SECONDS,
            )
            or DEFAULT_CODEX_AGENT_BACKGROUND_TIMEOUT_SECONDS
        )
        skill_name = (
            codex_skill_context.get("name")
            or codex_skill_context.get("relative_path")
            or "Codex skill"
        )
        skill_path = codex_skill_context.get("relative_path", "")

        def run_job() -> CodexSkillJobResult:
            response = self.llm_adapter.call_codex_agent_text(
                prompt,
                timeout=timeout_seconds,
            )
            success = response.provider != "error" and bool(response.content)
            return CodexSkillJobResult(
                success=success,
                content=response.content or "",
                error=None if success else (response.content or "Codex skill returned empty response"),
            )

        job = start_codex_skill_background_job(
            session_id=session_id,
            user_message=message,
            skill_name=skill_name,
            skill_path=skill_path,
            run=run_job,
        )
        if progress_callback:
            progress_callback({
                "type": "generating",
                "step": 1,
                "message": "后台任务已创建，结果会写入 skill_out。",
            })
        conversation_manager.add_message(session_id, "assistant", job.accepted_message)
        return AgentResult(
            success=True,
            content=job.accepted_message,
            dashboard=None,
            tool_calls_log=[],
            total_steps=1,
            provider="codex",
            model="codex",
            assistant_persisted=True,
        )

    def _build_codex_skill_agent_prompt(
        self,
        *,
        message: str,
        history: List[Dict[str, Any]],
        context: Dict[str, Any],
        codex_skill_context: Dict[str, str],
    ) -> str:
        """Build the prompt for native Codex skill execution."""
        skill_name = codex_skill_context.get("name") or codex_skill_context.get("relative_path") or "selected skill"
        safe_context = {
            key: value
            for key, value in (context or {}).items()
            if key not in {"skills", "codex_skill_id", "codex_skill_background"} and value not in (None, "")
        }
        history_lines: List[str] = []
        for item in (history or [])[-6:]:
            role = item.get("role")
            content = item.get("content")
            if role == "user" and content:
                history_lines.append(f"- 用户此前提问：{content}")

        sections = [
            f"请像在 Codex 中直接调用 skill 一样，使用 `{skill_name}` 处理下面的用户请求。",
            "这是 DSA 平台的自定义问询模式。不要使用 DSA 平台内置的行情/K线/技术/筹码/新闻工具，不要套用 DSA 默认问股模板。",
            "允许你按照该 Codex skill 的要求自行读取本地文件、联网搜索、调用 Codex 可用工具；如果某项能力在当前 Codex CLI 环境不可用，请说明后继续完成可行部分。",
            "除非用户明确要求修改项目文件，否则不要修改当前仓库。",
            "",
            "选中的 Codex skill 信息：",
            f"- name: {skill_name}",
            f"- source: {codex_skill_context.get('source')}",
            f"- path: {codex_skill_context.get('relative_path')}",
            "",
            "如果 Codex skill 自动加载没有生效，请按下面的 SKILL.md 内容执行：",
            "```markdown",
            codex_skill_context.get("content", ""),
            "```",
        ]
        if history_lines:
            sections.extend(["", "对话历史（仅供理解用户连续提问）：", "\n".join(history_lines)])
        if safe_context:
            sections.extend([
                "",
                "页面上下文（仅供参考，不是分析模板）：",
                json.dumps(safe_context, ensure_ascii=False, default=str),
            ])
        sections.extend(["", "用户当前请求：", message])
        return "\n".join(sections)

    @staticmethod
    def _load_codex_skill_context(skill_id: Any) -> Optional[Dict[str, str]]:
        """Load a selected Codex skill for direct prompt injection."""
        if not skill_id:
            return None
        try:
            from src.services.codex_skill_service import load_codex_skill_instructions

            return load_codex_skill_instructions(str(skill_id))
        except Exception:
            logger.warning("Failed to load Codex skill %s", skill_id, exc_info=True)
            return None

    def _codex_direct_timeout_seconds(self) -> Optional[float]:
        """Keep Codex Agent Q&A bounded by its dedicated runtime timeout."""
        config = getattr(self.llm_adapter, "_config", None)
        codex_timeout = float(getattr(config, "codex_exec_agent_timeout_seconds", 0) or 0)
        if codex_timeout <= 0:
            codex_timeout = float(getattr(config, "codex_exec_timeout_seconds", 0) or 0)
        agent_timeout = float(self.timeout_seconds or 0)
        candidates = [value for value in (codex_timeout, agent_timeout) if value > 0]
        return min(candidates) if candidates else None

    def _collect_codex_direct_context(
        self,
        *,
        stock_code: Optional[str],
        stock_name: str,
        progress_callback: Optional[Callable],
        tool_calls_log: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Collect standard stock data through existing Agent tools."""
        if not stock_code:
            return {"note": "未能从问题中识别出明确股票代码或名称。"}

        tool_plan = [
            ("get_realtime_quote", {"stock_code": stock_code}, "获取实时行情"),
            ("get_daily_history", {"stock_code": stock_code, "days": 90}, "获取K线数据"),
            ("analyze_trend", {"stock_code": stock_code}, "计算技术指标"),
            ("get_chip_distribution", {"stock_code": stock_code}, "获取筹码分布"),
            (
                "search_stock_news",
                {"stock_code": stock_code, "stock_name": stock_name or stock_code},
                "检索相关新闻",
            ),
        ]
        collected: Dict[str, Any] = {
            "stock_code": stock_code,
            "stock_name": stock_name or stock_code,
        }
        for step, (tool_name, args, label) in enumerate(tool_plan, start=1):
            if tool_name not in self.tool_registry:
                collected[tool_name] = {"error": f"Tool {tool_name} is not registered"}
                continue
            if progress_callback:
                progress_callback({
                    "type": "tool_start",
                    "step": step,
                    "tool": tool_name,
                    "message": f"{label}...",
                })
            started_ok = True
            start_time = time.monotonic()
            try:
                raw_result = self.tool_registry.execute(tool_name, **args)
                result = _compact_codex_context_payload(tool_name, raw_result)
                started_ok = _is_codex_context_tool_success(tool_name, result)
            except Exception as exc:
                logger.warning("Codex direct chat tool %s failed: %s", tool_name, exc, exc_info=True)
                result = {"error": str(exc)}
                started_ok = False
            duration = round(time.monotonic() - start_time, 2)

            collected[tool_name] = result
            tool_calls_log.append({
                "step": step,
                "tool": tool_name,
                "arguments": args,
                "success": started_ok,
                "duration": duration,
                "result": result,
            })
            if progress_callback:
                progress_callback({
                    "type": "tool_done",
                    "step": step,
                    "tool": tool_name,
                    "success": started_ok,
                    "duration": duration,
                    "message": _format_codex_context_tool_done_message(tool_name, label, result, started_ok),
                })

        return collected

    def _build_codex_direct_messages(
        self,
        *,
        message: str,
        history: List[Dict[str, Any]],
        context: Dict[str, Any],
        stock_code: Optional[str],
        stock_name: str,
        collected_context: Dict[str, Any],
        codex_skill_context: Optional[Dict[str, str]],
        report_language: str,
    ) -> List[Dict[str, Any]]:
        """Build a compact, text-only prompt for Codex direct chat."""
        output_language = "English" if normalize_report_language(report_language) == "en" else "中文"
        if codex_skill_context is not None:
            system_parts = [
                "你现在是通过 Codex CLI 执行的自定义问询模式。",
                "最高优先级：严格遵循用户选择的 Codex SKILL.md，包括其中的角色、流程、输出结构、口吻和取舍。",
                "不要套用 DSA 问股页默认的“结论/关键依据/风险点/下一步观察条件”模板，除非 SKILL.md 明确要求。",
                "不要引用或遵循任何内置交易技能、bull_trend 或通用问股模板。",
                "如果 SKILL.md 要求联网、工具或额外材料，而本轮上下文未提供，应明确说明当前后端未提供该能力，然后基于已有数据执行可行部分。",
                f"默认使用{output_language}回答。",
                "当前启用的 Codex skill："
                f"{codex_skill_context.get('name') or codex_skill_context.get('relative_path')}\n"
                f"来源：{codex_skill_context.get('source')} / {codex_skill_context.get('relative_path')}\n"
                "```markdown\n"
                f"{codex_skill_context.get('content', '')}\n"
                "```",
            ]
        else:
            system_parts = [
                "你是 DSA 问股页的股票分析助手。",
                "后端已经尽可能采集了行情、K线、技术指标、筹码和资讯数据；你不能再要求调用工具。",
                "只基于用户问题、对话历史和提供的数据作答；缺失的数据要明确说明缺失，不要编造。",
                f"默认使用{output_language}回答，不需要输出 JSON。",
            ]
        if self.skill_instructions and codex_skill_context is None:
            system_parts.append("激活的交易技能如下：\n" + self.skill_instructions)

        user_parts = [f"用户问题：{message}"]
        if stock_code:
            user_parts.append(f"识别股票：{stock_name or stock_code}（{stock_code}）")
        if context:
            safe_context = {
                key: value
                for key, value in context.items()
                if key not in {"skills", "codex_skill_id"} and value not in (None, "")
            }
            if safe_context:
                user_parts.append("[页面传入上下文]\n" + json.dumps(safe_context, ensure_ascii=False, default=str))
        data_label = "[DSA 提供的可选数据上下文]" if codex_skill_context is not None else "[后端采集数据]"
        user_parts.append(data_label + "\n" + json.dumps(collected_context, ensure_ascii=False, default=str))
        if codex_skill_context is not None:
            user_parts.append(
                "请直接按所选 Codex SKILL.md 处理用户问题。输出结构、标题、分析路径和侧重点由 SKILL.md 决定；"
                "DSA 数据上下文只是材料，不是回答模板。若用户问的是概念或模型身份等非个股问题，直接回答该问题。"
            )
        else:
            user_parts.append(
                "请给出直接回答：先给结论，再给关键依据、风险点和下一步观察条件。"
                "若用户问的是概念或模型身份等非个股问题，直接回答该问题。"
            )

        messages: List[Dict[str, Any]] = [{"role": "system", "content": "\n".join(system_parts)}]
        for item in (history or [])[-6:]:
            role = item.get("role")
            content = item.get("content")
            if codex_skill_context is not None and role != "user":
                continue
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": str(content)})
        messages.append({"role": "user", "content": "\n\n".join(user_parts)})
        return messages

    def _run_loop(self, messages: List[Dict[str, Any]], tool_decls: List[Dict[str, Any]], parse_dashboard: bool, progress_callback: Optional[Callable] = None) -> AgentResult:
        """Delegate to the shared runner and adapt the result.

        This preserves the exact same observable behaviour as the original
        inline implementation while sharing the single authoritative loop
        in :mod:`src.agent.runner`.
        """
        loop_result = run_agent_loop(
            messages=messages,
            tool_registry=self.tool_registry,
            llm_adapter=self.llm_adapter,
            max_steps=self.max_steps,
            progress_callback=progress_callback,
            max_wall_clock_seconds=self.timeout_seconds,
        )

        model_str = loop_result.model

        if parse_dashboard and loop_result.success:
            dashboard = parse_dashboard_json(loop_result.content)
            return AgentResult(
                success=dashboard is not None,
                content=loop_result.content,
                dashboard=dashboard,
                tool_calls_log=loop_result.tool_calls_log,
                total_steps=loop_result.total_steps,
                total_tokens=loop_result.total_tokens,
                provider=loop_result.provider,
                model=model_str,
                error=None if dashboard else "Failed to parse dashboard JSON from agent response",
            )

        return AgentResult(
            success=loop_result.success,
            content=loop_result.content,
            dashboard=None,
            tool_calls_log=loop_result.tool_calls_log,
            total_steps=loop_result.total_steps,
            total_tokens=loop_result.total_tokens,
            provider=loop_result.provider,
            model=model_str,
            error=loop_result.error,
        )

    def _build_user_message(self, task: str, context: Optional[Dict[str, Any]] = None) -> str:
        """Build the initial user message."""
        parts = [task]
        if context:
            report_language = normalize_report_language(context.get("report_language", "zh"))
            if context.get("stock_code"):
                parts.append(f"\n股票代码: {context['stock_code']}")
            if context.get("report_type"):
                parts.append(f"报告类型: {context['report_type']}")
            if report_language == "en":
                parts.append("输出语言: English（所有 JSON 键名保持不变，所有面向用户的文本值使用英文）")
            else:
                parts.append("输出语言: 中文（所有 JSON 键名保持不变，所有面向用户的文本值使用中文）")

            # Inject pre-fetched context data to avoid redundant fetches
            if context.get("realtime_quote"):
                parts.append(f"\n[系统已获取的实时行情]\n{json.dumps(context['realtime_quote'], ensure_ascii=False)}")
            if context.get("chip_distribution"):
                parts.append(f"\n[系统已获取的筹码分布]\n{json.dumps(context['chip_distribution'], ensure_ascii=False)}")
            if context.get("news_context"):
                parts.append(f"\n[系统已获取的新闻与舆情情报]\n{context['news_context']}")

        parts.append("\n请使用可用工具获取缺失的数据（如历史K线、新闻等），然后以决策仪表盘 JSON 格式输出分析结果。")
        return "\n".join(parts)
