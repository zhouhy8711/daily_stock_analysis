# 规则模块设计方案

## 背景

规则模块用于让用户在 Web 端配置选股、观察和触发分析的条件。模块入口放在 Web 侧边栏的「首页」和「问股」之间，作为从日常看盘进入智能分析前的一层规则筛选能力。

本方案先落地可解释、可回测、可扩展的 MVP，不在第一版支持「上穿 / 下穿」。这两个操作符的数学语义和实盘容错边界需要单独评审，避免过早固化。

## 目标

- 支持用户创建多个规则。
- 每条规则由多个条件组组成，条件组之间是「或」关系。
- 每个条件组内可以有多个子条件，子条件之间是「且」关系。
- 子条件可以选择已有指标 key，并与固定值、其他指标或历史聚合值比较。
- 支持前 N 个周期的最大值、最小值、平均值等历史聚合。
- 支持连续 N 次满足、近 N 次至少 M 次满足等时序条件。
- 支持手动运行规则，并区分“最新日扫描”和“历史回测”：前者只判断每只股票最后一个交易日，后者在历史窗口内逐个交易日判断。
- 运行结果展示命中股票、命中日期、命中条件组、指标快照和解释文本；多条件组规则会按实际命中的条件组拆分结果列，避免用其他条件组的指标列展示为空。
- 后续可以接入定时运行、通知推送、问股和回测。

## 非目标

- 第一版不支持「上穿 / 下穿」。
- 第一版不做复杂表达式脚本执行，避免引入安全边界问题。
- 第一版不做全市场高频扫描。
- 第一版不把规则运行失败作为主分析流程失败条件。
- 第一版不接入自动下单或交易执行。

## 信息架构

侧边栏导航顺序：

1. 首页
2. 规则
3. 问股
4. 持仓
5. 回测
6. 设置

规则页面建议分为三块：

- 左侧：规则列表、启用状态、最近运行时间、最近命中数。
- 中间：规则编辑器，包括基础信息、股票范围、条件组和子条件。
- 右侧或底部：运行模式、运行结果、命中交易日、命中解释、错误信息。

## 逻辑模型

规则采用固定两层逻辑：

```text
规则 = 条件组 A OR 条件组 B OR 条件组 C
条件组 = 子条件 1 AND 子条件 2 AND 子条件 3
```

页面文案可以表达为：

- 满足以下任一条件组。
- 每个条件组内需同时满足所有子条件。

## DSL 草案

```json
{
  "name": "放量突破观察",
  "description": "收盘价创新高且成交量放大",
  "is_active": true,
  "is_disable": false,
  "period": "daily",
  "lookback_days": 120,
  "target": {
    "scope": "custom",
    "stock_codes": ["600519", "000001"]
  },
  "groups": [
    {
      "id": "group-1",
      "conditions": [
        {
          "id": "cond-1",
          "left": {
            "metric": "close",
            "offset": 0
          },
          "operator": ">",
          "right": {
            "type": "aggregate",
            "metric": "close",
            "method": "max",
            "window": 20,
            "offset": 1
          }
        },
        {
          "id": "cond-2",
          "left": {
            "metric": "volume",
            "offset": 0
          },
          "operator": ">",
          "right": {
            "type": "aggregate",
            "metric": "volume",
            "method": "avg",
            "window": 5,
            "offset": 1,
            "multiplier": 1.5
          }
        }
      ]
    }
  ]
}
```

`scope=watchlist` 时，Web 页面会按首页自选监控区的同一逻辑生成当前自选列表：优先使用 `STOCK_LIST`，未配置时回退最近历史股票，并在股票清单中展示“代码 + 名称”；`scope=all_a_shares` 时会从前端股票索引读取所有 A 股并填入同一清单，后端在未收到显式 `stock_codes` 时也会读取同一股票索引兜底解析全量 A 股，避免后台任务以空目标运行；`scope=custom` 保留手工维护列表。股票清单支持最大化查看，并在最大化状态下按代码或名称筛选；每行股票前提供移除按钮，便于整理全量 A 股扫描范围。保存和手动运行规则时仍只从清单中提取股票代码写入 `stock_codes`，后端优先扫描该列表，旧规则未保存列表时才回退读取当前 `STOCK_LIST`。

## 指标 Key

指标不直接写死在页面里，而是通过指标注册表暴露。每个指标 key 包含展示名、单位、类型、可用周期和支持的关系。

第一版按指标分析页的大图表区域分组支持，并与指标分析页可点加号加入规则的指标保持同一套 key：

- 核心行情：`current_price`、`change`、`change_percent`、`total_mv`、`circ_mv`、`pe_ratio`、`pe_ratio_percentile_250d`（市盈TTM 250日分位）
- K线图：`open`、`high`、`low`、`close`、`prev_close`、`pct_chg`、`amplitude`、`price_range_30d_pct`、`price_range_60d_pct`、`limit_up_price`、`limit_down_price`、`price_speed`、`entrust_ratio`、`ma5`、`ma10`、`ma20`、`ma30`、`ma60`、`volume_ratio`、`total_shares`、`float_shares`
- 趋势起涨：`bias_ma5_pct`（偏离 MA5）、`ma_bullish_alignment_signal`（均线多头排列）、`ma_uptrend_signal`（均线上行）、`price_breakout_20d_signal`（突破 20 日高点）、`prior_10d_breakout_20d_count`（前 10 日 20 日突破次数）、`price_breakout_60d_signal`（突破 60 日高点）、`volume_expansion_20d_ratio`（成交量 / 20 日均量）、`volume_expansion_signal`（放量确认）、`trend_start_signal`（趋势起涨复合信号）、`trend_live_setup_score`（实盘起涨观察分）、`trend_live_watch_signal`（实盘起涨观察信号）、`trend_live_confirm_signal`（实盘起涨确认信号）、`trend_overheat_risk_score`（趋势追高风险分）、`trend_overheat_risk_signal`（趋势追高风险信号）、`trend_failure_signal`（趋势起涨失效信号）、`history_trading_days_count`（历史交易日数）
- 成交量图：`volume`、`after_hours_volume`、`amount`、`after_hours_amount`、`volume_ma5`、`volume_ma10`、`volume_ma20`、`amount_ma5`、`amount_ma10`
- MACD图：`ema12`、`ema26`、`macd_dif`、`macd_dea`、`macd`
- RSI图：`rsi6`、`rsi12`、`rsi24`
- 筹码峰-全部筹码：`profit_ratio`（收盘获利）、`trapped_ratio`（套牢盘）、`profit_trapped_spread`、`avg_cost`、`price_to_avg_cost_pct`、`cost_90_low`、`cost_90_high`、`price_range_90_mid`、`price_range_90_width`、`price_range_90_width_pct`、`chip_concentration_90`、`chip_concentration_90_avg_30d`、`chip_concentration_90_avg_60d`、`cost_70_low`、`cost_70_high`、`price_range_70_mid`、`price_range_70_width`、`price_range_70_width_pct`、`chip_concentration_70`、`chip_peak_price`、`chip_peak_percent`、`chip_peak_distance_pct`、`chip_peak_count`、`chip_single_peak_signal`、`chip_peak_low_price`、`chip_peak_high_price`、`chip_peak_price_ratio`
- 筹码峰-主力筹码：`main_profit_ratio`、`main_trapped_ratio`、`main_profit_trapped_spread`、`main_avg_cost`、`main_price_to_avg_cost_pct`、`main_cost_90_low`、`main_cost_90_high`、`main_price_range_90_mid`、`main_price_range_90_width`、`main_price_range_90_width_pct`、`main_chip_concentration_90`、`main_cost_70_low`、`main_cost_70_high`、`main_price_range_70_mid`、`main_price_range_70_width`、`main_price_range_70_width_pct`、`main_chip_concentration_70`、`main_chip_peak_price`、`main_chip_peak_percent`、`main_chip_peak_distance_pct`
- 实时监控：`turnover_rate`、`main_net_volume_pct`、`main_force_net`、`net_super_large_order`、`net_large_order`、`net_medium_order`、`net_small_order`
- 财务事件：`deducted_net_profit_yoy_pct`（扣非净利同比）、`deducted_net_profit_qoq_pct`（扣非净利环比）、`announcement_next_day_gap_pct`（公告次日跳空缺口）、`announcement_next_day_volume_ratio`（公告次日量能 / 前 5 日均量）、`announcement_next_day_gap_unfilled`（公告次日缺口未回补，1/0）、`net_profit_gap_signal`（净利润断层信号）

财务事件类指标在规则扫描时会写入 `stock_daily` 对应公告后首个交易日，并同步到本轮规则扫描的 `history_by_code` 与 `earnings_gap_metrics_by_code` 缓存；后续 DB-only 回测和 K 线缓存读取可复用这些指标。
- 额外：`prev_5d_return_pct`（前5日累计涨幅，不含当前判断日）、`prev_20d_return_pct`（前20日累计涨幅，不含当前判断日）

`trend_start_signal` 用于把趋势股起涨点抽象成可回测条件：收盘价站上 MA5，且 MA5 > MA10 > MA20 > MA30，MA5/10/20 同步上行；收盘价突破前 20 日或 60 日高点；当日成交量至少为 20 日均量的 1.2 倍；MACD 满足 DIF > DEA > 0 且柱体为正；同时收盘价偏离 MA5 不超过 15%，避免把已经明显过热的连续加速段当作初始起涨点。

`trend_live_*` 指标用于把复盘中的“起涨前技术共振”改写成实盘可执行规则，计算时只读取当前及过去 K 线，不使用未来涨幅：

- `trend_live_setup_score` 按 MACD 金叉/翻红或同步抬升、MA5 上穿或高于 MA10、收盘站上 MA20、MA10 高于 MA20、量比或成交量相对 20 日均量放大、RSI6 回到强势区、接近或突破 20 日高位、MA5/10/20 同步上行进行加权，分数越高说明起涨共振越充分。
- `trend_live_watch_signal` 在观察分达到 7 分、未过热且未失效时记为 1，适合作为加入观察池或触发问股分析的条件。
- `trend_live_confirm_signal` 在观察分达到 9 分，并同时满足 20 日高位突破、放量、MACD 多头或当日转强、未过热且未失效时记为 1，适合作为更严格的实测/回测命中条件。
- `trend_overheat_risk_score` 会根据前 5 日涨幅超过 35%、前 20 日涨幅超过 80%、偏离 MA5 超过 15%、RSI6 超过 88、单日涨幅过大、放量长阳一致性等追高特征加分；`trend_overheat_risk_signal` 在风险分达到 3 分时记为 1。
- `trend_failure_signal` 在收盘跌破 MA20，或跌破 MA10 且 MACD 转弱，或大跌并跌破 MA5 时记为 1，适合用于取消观察、退出规则或风控过滤。

`pe_ratio_percentile_250d` 用近 250 个交易日的正 PE 样本计算当前 PE 分位。用于从“技术突破很多”中继续筛出高估值/高预期状态的股票；如果历史不足 80 个正 PE 样本，指标为空，规则条件不会误命中。

截图趋势起涨综合规则当前包含两个互斥的固定形态分支：

- 成熟趋势股分支：要求高 PE（`pe_ratio >= 50`）且 250 日 PE 分位 `>= 90`，20 个交易日未突破后的首次 20 日高点突破，并同时满足放量、MACD 多头、MACD 柱上升、MA 上行、RSI 强势但不过热、MA5 乖离受控、前 5/20 日涨幅不过热。
- 新股/次新股分支：用于历史交易日数 20-80 天、无法形成 250 日 PE 分位的股票，要求 20 日突破、前 10 日未出现 20 日突破、MACD 多头、成交量不低于 20 日均量 0.8 倍、RSI 强势、MA5 乖离处于 4%-30%、前 5 日涨幅不超过 50%、当日涨幅 4%-20%、未触发趋势失效。

财务事件指标会在规则扫描时尝试读取公开财务数据源的扣非净利报告事件，并把事件映射到公告后的第一个交易日。以“净利润断层”为例，可配置为：扣非净利同比 `>= 100`、扣非净利环比 `>= 50`、公告次日跳空缺口 `>= 3`、公告次日量能 / 前 5 日均量 `>= 1.5`、公告次日缺口未回补 `= 1`。若数据源缺少公告日期或扣非净利字段，对应股票不会误判命中。

指标分析页点击加号会先把多个指标保存为一个规则草稿，已选指标的按钮会切换为减号，点击可直接从草稿移除。K 线标题栏中的股本、涨跌停、涨速、主力资金和委比等次级指标收纳在“更多”浮层中，浮层内仍保留同样的加号入口。右上角“已选 N”可打开浮窗编辑关系、取值日偏移和值类型等条件配置；跳转到规则页后会生成一条未保存规则，这些指标会放在同一个条件组里，因此默认是「且」关系，用户仍可在规则页继续调整比较关系和阈值后保存。

主力持仓、行业和新闻情绪类指标可作为二期扩展。

## 右侧值类型

子条件右侧值支持多种来源：

- 固定数值：如 `10`、`1.5`、`30`。
- 指标引用：如 `close`、`ma20`、`volume_ratio`。
- 历史聚合：前 N 周期的最大值、最小值、平均值、求和、中位数、标准差。
- 区间：如 `10 到 20`。
- 倍数表达：通过 `multiplier` 表示，例如前 5 日平均成交量的 1.5 倍。

示例：

```text
当前收盘价 > 前 20 日最高收盘价
当前成交量 > 前 5 日平均成交量 * 1.5
当前 RSI6 < 30
当前换手率 介于 3 到 8
```

## 操作符

第一版支持：

- 比较：`>`、`>=`、`<`、`<=`、`=`、`!=`
- 区间：`between`、`not_between`
- 连续：`consecutive`
- 频次：`frequency`
- 趋势：`trend_up`、`trend_down`
- 新高新低：`new_high`、`new_low`
- 存在性：`exists`、`not_exists`
- 价格形态：`sandwich_number`（夹板数，最新价元角分呈 `a.ba`，即元位和分位相同且角位不同）、`pair_number`（对子数，最新价角分呈 `.bb`，即角位和分位相同）

暂不支持：

- `cross_up`
- `cross_down`

## 连续与频次

连续 N 次满足：

```json
{
  "left": { "metric": "close", "offset": 0 },
  "operator": "consecutive",
  "compare": ">",
  "right": { "type": "metric", "metric": "ma20", "offset": 0 },
  "lookback": 3
}
```

含义：

```text
最近 3 个周期，收盘价都大于 MA20。
```

近 N 次至少 M 次满足：

```json
{
  "left": { "metric": "volume_ratio", "offset": 0 },
  "operator": "frequency",
  "compare": ">",
  "right": { "type": "literal", "value": 1.5 },
  "lookback": 10,
  "min_count": 6
}
```

含义：

```text
最近 10 个周期中，至少 6 次量比大于 1.5。
```

## 后端模块

建议新增：

- `api/v1/endpoints/rules.py`：规则 CRUD、指标注册表、规则运行。
- `api/v1/schemas/rules.py`：规则 DSL 和 API Schema。
- `src/services/rule_service.py`：规则校验、运行编排、结果组装。
- `src/repositories/rule_repo.py`：规则定义、运行记录、命中结果持久化。
- `src/rules/metrics.py`：指标注册表和指标计算。
- `src/rules/engine.py`：条件表达式求值。

## 存储模型

建议新增三张表：

- `stock_rules`：规则定义，保存基础信息、股票范围和 JSON DSL。
  - `is_disable` 默认为 `false`；为 `true` 时规则列表接口不返回该规则，Web 规则页和回测/实测规则选择中不会展示。
- `stock_rule_runs`：规则运行记录，保存状态、目标数量、命中数量、耗时和错误。
- `stock_rule_matches`：规则命中结果，保存股票代码、命中日期、命中事件、命中条件组、指标快照和解释文本。历史回测会把同一股票的多个命中交易日保存为 `matched_events`，前端按“股票 + 日期”展开展示。

## MVP 边界

第一期实现：

- 新增 Web「规则」页面。
- 支持创建、编辑、删除、启停规则。
- 支持自选股和自定义股票列表作为运行范围。
- 支持日线周期。
- 支持基础行情、均线、成交量、MACD、RSI 等指标。
- 支持筹码集中度、解套率和平均筹码成本等筹码指标。
- 支持固定值、指标引用、历史聚合和区间。
- 支持连续 N 次和近 N 次至少 M 次。
- 支持手动运行并展示命中股票与历史命中日期。
- 支持“最新日扫描”和“历史回测”两种运行模式，避免只看最新交易日的监控与逐日回测混用同一语义。
- Web 实测与后端 `latest + db_only` 运行只在 A 股交易日 15:00 及以前触发，休市后不再用旧本地行情重复生成命中结果；每次实测使用独立 `live_cache_key` 建立 live 数据缓存，09:30 前触发时只预热选中规则和股票需要的当天前历史日线缓存，并返回 `prewarm_only`，不读取分钟热表、不评估命中、不通知；9:30 后实测 quote 从 `stock_intraday_minute` 聚合，并持续复用该 live 缓存中的历史、基础筹码分布和财务事件派生数据，当前判断日会先合成实时 K 线并重算当日筹码分布，缺少换手率时才按最新价重估基础获利盘兜底；停止实测时清理对应缓存；实测不读实时快照或远程行情源。
- Web 规则历史回测使用异步后台任务执行；执行中持续展示已完成股票数 / 总股票数，全部完成后再加载命中结果。多规则回测按股票共享一次历史行情读取与指标帧构建，未使用筹码类指标时跳过筹码计算。规则实测和回测统一强制 `db_only`，只读 `stock_daily`、`stock_intraday_minute`、`stock_chip_daily` 及已落库财务派生字段；缺失数据需通过离线任务或补数据脚本补齐。点击命中记录打开指标弹窗时也使用历史 DB-only 模式，不触发实时行情、资金流、主力持仓或筹码 HTTP 请求；命中日仍作为红色高亮和筹码快照锚点，K 线、成交量与 MACD 图保留命中日之后已落库的交易日，便于观察后续表现；命中表的股票、行业和日期表头可点击切换升降序排序。

第二期再做：

- 全市场扫描。
- 定时运行。
- 命中后通知推送。
- 命中后触发问股或生成分析报告。
- 与回测模块联动评估规则有效性。
- 单独重新设计「上穿 / 下穿」。
