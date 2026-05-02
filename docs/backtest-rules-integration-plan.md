# 回测模块使用调研与规则模块联动方案

## 背景与结论

当前项目里已经有两套相邻但尚未打通的能力：

- 回测模块：验证历史 AI 分析记录的 `AnalysisHistory.operation_advice`，用分析日之后的日线数据判断方向、胜负、止盈止损和模拟收益。
- 规则模块：支持用户配置规则、手动运行规则，并保存命中股票、命中日期、指标快照和解释文本。

这也是现在“策略回测 / 规则回测用不起来”的主要原因：Web 回测页虽然叫策略回测，但实际只能回测历史 AI 分析结果，不能选择规则；规则模块虽然能找出历史命中事件，但没有信号方向语义，也没有把命中事件送进回测引擎。

建议第一阶段先把规则回测做成独立链路：规则命中默认按看多 / 买入 / long 解释，同时允许规则或单次回测覆盖方向；历史命中事件由规则引擎产生，收益和胜负判定复用现有 `BacktestEngine.evaluate_single`。

## 现有回测模块使用调研

### 代码入口

现有回测能力的核心入口如下：

- CLI：`python main.py --backtest`
- CLI 单股：`python main.py --backtest --backtest-code 600519`
- CLI 指定窗口：`python main.py --backtest --backtest-days 1`
- CLI 强制重算：`python main.py --backtest --backtest-force`
- 自动回测：每日分析流程结束后，如果 `BACKTEST_ENABLED=true`，会自动调用 `BacktestService.run_backtest`
- API：`POST /api/v1/backtest/run`
- API：`GET /api/v1/backtest/results`
- API：`GET /api/v1/backtest/performance`
- API：`GET /api/v1/backtest/performance/{code}`
- Web：`apps/dsa-web/src/pages/BacktestPage.tsx`

### 核心数据流

现有回测数据流是：

1. `BacktestRepository.get_candidates` 从 `analysis_history` 选择已过冷却期的历史分析记录。
2. `BacktestService._resolve_analysis_date` 从 `context_snapshot.enhanced_context.date` 解析分析日期；如果没有，则退回 `created_at.date()`。
3. `StockRepository.get_start_daily` 读取分析日或后续第一个有效交易日的收盘价。
4. `StockRepository.get_forward_bars` 读取评估窗口内的前向日线。
5. `BacktestEngine.evaluate_single` 根据 `operation_advice` 推断仓位和方向，再计算窗口收益、方向命中、胜负、止盈止损命中和模拟收益。
6. `backtest_results` 保存单条分析记录的回测结果。
7. `backtest_summaries` 保存整体和单股汇总。

现有回测模块验证的是“AI 当时给出的操作建议是否有效”，不是规则条件本身。

### 配置项

相关配置在 `.env.example`、`src/config.py` 和配置注册表里已有默认值：

| 配置 | 默认值 | 作用 |
| --- | --- | --- |
| `BACKTEST_ENABLED` | `true` | 每日分析后是否自动触发回测 |
| `BACKTEST_EVAL_WINDOW_DAYS` | `10` | 默认评估窗口，单位为交易日 |
| `BACKTEST_MIN_AGE_DAYS` | `14` | 只回测至少 N 天前的分析记录，避免前向数据不完整 |
| `BACKTEST_ENGINE_VERSION` | `v1` | 回测逻辑版本，用于区分旧结果 |
| `BACKTEST_NEUTRAL_BAND_PCT` | `2.0` | 中性区间阈值，窗口收益落在正负该阈值内视为震荡 |

### 操作建议映射

`BacktestEngine` 通过关键词把 `operation_advice` 映射为仓位和方向：

| 建议文本 | 仓位 | 预期方向 | 说明 |
| --- | --- | --- | --- |
| `买入` / `加仓` / `strong buy` / `buy` | `long` | `up` | 预期上涨 |
| `卖出` / `减仓` / `strong sell` / `sell` | `cash` | `down` | 预期下跌，空仓规避 |
| `持有` / `hold` | `long` | `not_down` | 持有，只要不显著下跌就算方向成立 |
| `观望` / `等待` / `wait` | `cash` | `flat` | 预期震荡 |
| 无法识别 | `cash` | `flat` | 默认空仓震荡 |

### 为什么当前容易“用不起来”

常见原因如下：

- `processed=0`：没有满足 `BACKTEST_MIN_AGE_DAYS` 的历史分析记录。
- `processed=0`：对应窗口和引擎版本已经回测过，且没有传 `force=true` 或 `--backtest-force`。
- `insufficient > 0`：分析日收盘价或前向 K 线不足。
- `errors > 0`：历史记录缺少可解析分析日期、数据字段异常，或补齐日线失败。
- Web 回测页只支持股票代码、分析日期、窗口和 force，不能选择规则或策略。
- `BacktestService.get_skill_summary` 当前明确返回 `None`，Agent skill / strategy 粒度回测汇总还没有真实持久化数据。
- 文档和页面的“策略回测”叫法容易让用户以为可以测试规则或内置策略，但现有实现只是 AI 历史分析记录回测。

### 当前可用的最小验证路径

如果只验证现有 AI 分析回测，可以按下面流程走：

1. 先确认数据库中已有 `analysis_history` 记录，并且记录有 `operation_advice`。
2. 用 1 日窗口和强制重算降低前向数据要求：

```bash
python main.py --backtest --backtest-days 1 --backtest-force
```

3. 如果只看单股：

```bash
python main.py --backtest --backtest-code 600519 --backtest-days 1 --backtest-force
```

4. 或通过 API：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/backtest/run \
  -H 'Content-Type: application/json' \
  -d '{"force": true, "eval_window_days": 1, "min_age_days": 0}'
```

5. 查看结果：

```bash
curl "http://127.0.0.1:8000/api/v1/backtest/results?page=1&limit=20&eval_window_days=1"
```

## 现有规则模块调研

### 当前能力

规则模块当前包含：

- 后端 API：`/api/v1/rules`
- Schema：`api/v1/schemas/rules.py`
- 服务层：`src/services/rule_service.py`
- 仓储层：`src/repositories/rule_repo.py`
- 规则引擎：`src/rules/engine.py`
- 指标注册表和指标计算：`src/rules/metrics.py`
- Web 页面：`apps/dsa-web/src/pages/RulesPage.tsx`

规则 DSL 是两层结构：

```text
规则 = 条件组 A OR 条件组 B OR 条件组 C
条件组 = 子条件 1 AND 子条件 2 AND 子条件 3
```

规则运行链路是：

1. `RuleService.run_rule` 读取规则定义。
2. 根据规则目标范围选择 `watchlist` 或 `custom` 股票列表。
3. `StockService.get_history_data` 获取历史日线。
4. `build_metric_frame` 计算均线、MACD、RSI、筹码等指标。
5. `evaluate_rule_history` 扫描整段历史并返回命中事件。
6. `stock_rule_runs` 保存运行记录。
7. `stock_rule_matches` 保存命中股票、命中日期、条件组、快照和解释。

### 当前缺口

规则模块要成为真正可回测模块，还缺三类能力：

- 信号语义：规则命中到底表示买入、卖出、观望，当前定义里没有字段。
- 回测样本：`stock_rule_matches` 是一次手动运行的结果快照，不是可复算、可聚合的回测结果。
- 汇总维度：现有 `backtest_summaries` 只有 overall / stock，没有 rule / rule+stock 维度。

## 规则与回测打通方案

### 设计原则

- 不把规则命中伪装成 `AnalysisHistory`，避免污染 AI 分析历史。
- 复用现有纯逻辑回测引擎，避免新增一套收益和胜负判定。
- 规则回测结果独立存储，方便后续按规则、股票、窗口和方向聚合。
- 第一版先做日线长多 / 空仓评估，不引入实盘交易撮合、滑点、手续费和仓位管理。
- 规则命中默认看多，但 UI、API 和文档都要明确展示这个默认。

### 信号方向语义

第一版建议给规则或回测请求增加 `signal_direction`：

| 值 | 等价建议 | 仓位 | 预期方向 | 适用场景 |
| --- | --- | --- | --- | --- |
| `bullish` | `买入` | `long` | `up` | 默认值，适合买点、突破、放量等规则 |
| `bearish` | `卖出` | `cash` | `down` | 风险、破位、超买回落等规则 |
| `flat` | `观望` | `cash` | `flat` | 震荡、等待确认类规则 |
| `not_down` | `持有` | `long` | `not_down` | 持仓跟踪、趋势延续类规则 |

默认策略：

- 规则未配置方向时，按 `bullish` 回测。
- 单次回测请求传入方向时，以请求为准。
- 后续实现规则编辑器时，可以把方向放在“基础设置”里，默认显示“看多（默认）”。

### 回测数据流

规则回测建议新增一个服务层，例如 `RuleBacktestService`：

1. 读取 `StockRule` 定义并校验。
2. 解析目标股票范围。
3. 对每只股票获取足够长的历史日线。
4. 用 `build_metric_frame` 构造指标帧。
5. 用 `evaluate_rule_history` 得到历史命中事件。
6. 对每个命中事件：
   - 使用命中日期作为 `signal_date`。
   - 使用命中日收盘价或后续可交易日收盘价作为 `start_price`。
   - 使用命中日之后的前向日线作为 `forward_bars`。
   - 根据 `signal_direction` 转成 `operation_advice`。
   - 调用 `BacktestEngine.evaluate_single`。
7. 保存 `rule_backtest_results`。
8. 聚合生成 `rule_backtest_summaries`。

### 建议存储模型

新增 `rule_backtest_results`：

| 字段 | 说明 |
| --- | --- |
| `id` | 主键 |
| `rule_id` | 规则 ID |
| `stock_code` | 股票代码 |
| `stock_name` | 股票名称 |
| `signal_date` | 规则命中日期 |
| `signal_direction` | `bullish` / `bearish` / `flat` / `not_down` |
| `matched_groups_json` | 命中的条件组 |
| `snapshot_json` | 命中日指标快照 |
| `explanation` | 命中解释 |
| `eval_window_days` | 评估窗口 |
| `engine_version` | 回测引擎版本 |
| `eval_status` | `completed` / `insufficient_data` / `error` |
| `evaluated_at` | 回测时间 |
| `start_price` / `end_close` / `max_high` / `min_low` | 价格字段 |
| `stock_return_pct` / `simulated_return_pct` | 收益字段 |
| `direction_expected` / `direction_correct` / `outcome` | 方向和胜负 |
| `first_hit` / `first_hit_date` / `first_hit_trading_days` | 止盈止损命中 |

唯一约束建议：

```text
rule_id + stock_code + signal_date + signal_direction + eval_window_days + engine_version
```

新增 `rule_backtest_summaries`：

| 字段 | 说明 |
| --- | --- |
| `scope` | `rules_overall` / `rule` / `rule_stock` |
| `rule_id` | 规则 ID，整体汇总可为空 |
| `stock_code` | 股票代码，规则整体汇总可为空 |
| `signal_direction` | 方向，可为空表示全部方向 |
| `eval_window_days` | 评估窗口 |
| `engine_version` | 引擎版本 |
| 现有汇总指标 | 复用 `BacktestSummary` 的计数、胜率、方向准确率、平均收益、止盈止损统计、诊断 JSON |

### API 设计草案

建议把规则回测放在 backtest 域下，避免 rules API 承担过多分析职责：

| API | 方法 | 说明 |
| --- | --- | --- |
| `/api/v1/backtest/rules/{rule_id}/run` | POST | 触发单条规则回测 |
| `/api/v1/backtest/rules/{rule_id}/results` | GET | 查询单条规则回测明细 |
| `/api/v1/backtest/rules/{rule_id}/performance` | GET | 查询单条规则汇总 |
| `/api/v1/backtest/rules/performance` | GET | 查询全部规则整体汇总 |

`POST /api/v1/backtest/rules/{rule_id}/run` 请求建议支持：

```json
{
  "code": "600519",
  "force": false,
  "eval_window_days": 10,
  "min_age_days": 14,
  "signal_direction": "bullish",
  "signal_date_from": "2024-01-01",
  "signal_date_to": "2024-12-31",
  "limit": 2000
}
```

说明：

- `code` 为空时按规则目标范围运行。
- `signal_direction` 为空时使用规则默认方向；规则也未配置时使用 `bullish`。
- `min_age_days` 用于避免最近命中事件没有足够前向 K 线。
- `limit` 限制最大命中事件数，避免一次规则扫描写入过多结果。

### Web 交互方案

建议 Web 做两处入口：

- 回测页增加模式切换：`AI 分析回测` / `规则回测`。
- 规则页运行结果区增加“回测此规则”按钮，跳转到回测页并带上 `rule_id`。

规则回测模式的筛选项：

- 规则选择器。
- 股票代码筛选。
- 评估窗口。
- 信号方向。
- 命中日期范围。
- Force 重算。

结果表新增或替换列：

- 规则名。
- 股票。
- 命中日期。
- 信号方向。
- 命中解释。
- 窗口收益。
- 方向命中。
- outcome。
- eval status。

### 与现有 Agent / Skill 回测的关系

当前 `get_skill_summary` 明确返回 `None`，这是正确的保护：没有真实 skill / rule 维度持久化结果时，不应伪造汇总。

规则回测落地后，可以再评估是否让 Agent 使用规则回测表现：

- `get_rule_backtest_summary(rule_id)`：读取真实规则回测汇总。
- skill 自动加权仍保持原逻辑，除非内置 skill 和规则有明确映射表。
- 不建议把 rule summary 直接塞进 `get_skill_summary`，除非产品上明确“某 skill 等价某规则”。

## 实施分期建议

### Phase 1：后端最小闭环

- 新增 `RuleBacktestService`。
- 新增独立存储表。
- 新增规则回测 API。
- 复用 `BacktestEngine.evaluate_single` 和 `BacktestEngine.compute_summary`。
- 完成单条规则、单股、1 日窗口的最小回测闭环。

### Phase 2：Web 可用

- 回测页增加规则模式。
- 规则页增加“回测此规则”入口。
- 展示规则汇总和明细。
- 明确展示默认方向，避免误解。

### Phase 3：自动化与智能使用

- 支持定时规则回测。
- 支持规则表现排行。
- 支持 Agent 查询规则回测表现。
- 视数据质量再引入手续费、滑点、开盘价成交、持有期去重等更真实的交易模拟。

## 测试方案

后续实现代码时建议补充：

- 后端单测：规则命中生成多个历史 signal。
- 后端单测：默认 `bullish` 和请求覆盖方向都能得到正确 `direction_expected`。
- 后端单测：force 重算不会触发唯一约束错误。
- 后端单测：前向 K 线不足时写入 `insufficient_data`。
- 后端单测：规则级和规则+股票级 summary 聚合正确。
- API 测试：run / results / performance 的成功、404、参数错误。
- 前端测试：回测页规则模式筛选、运行、空态和错误态。
- 回归测试：现有 AI 回测、规则运行不受影响。

## 文档与发布注意事项

本文件是调研和实现方案，不代表功能已经完成。

真正实现规则回测后，需要同步更新：

- `docs/CHANGELOG.md`
- `docs/full-guide.md`
- `docs/full-guide_EN.md`
- `README.md`
- 必要时同步 `docs/README_EN.md`、`docs/README_CHT.md`

本次只新增方案文档，不更新 README，原因是这不是已发布用户能力；也不更新 `docs/CHANGELOG.md`，原因是调研文档本身不改变运行行为。

## 风险与取舍

- 默认看多会让第一版更容易使用，但必须在 UI 和 API 响应里明确显示，避免用户把观察规则误认为买入规则。
- 独立规则回测表会增加存储和 API 面，但能避免污染 AI 分析历史，也便于未来做规则排行。
- 复用现有 `BacktestEngine` 可以快速闭环，但它仍是简化的 long-only 日线评估，不等价真实交易系统。
- 如果后续要引入手续费、滑点、开盘成交、仓位和多信号冲突处理，应作为下一层交易模拟引擎设计，不要塞进规则条件求值层。
