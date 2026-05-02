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
- 支持手动运行规则，展示命中股票、命中日期、命中条件组和解释文本。
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
- 右侧或底部：运行结果、命中交易日、命中解释、错误信息。

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

`scope=watchlist` 时，Web 页面会按首页自选监控区的同一逻辑生成当前自选列表：优先使用 `STOCK_LIST`，未配置时回退最近历史股票，并在股票清单中展示“代码 + 名称”；`scope=all_a_shares` 时会从前端股票索引读取所有 A 股并填入同一清单；`scope=custom` 保留手工维护列表。股票清单支持最大化查看，并在最大化状态下按代码或名称筛选；每行股票前提供移除按钮，便于整理全量 A 股扫描范围。保存和手动运行规则时仍只从清单中提取股票代码写入 `stock_codes`，后端优先扫描该列表，旧规则未保存列表时才回退读取当前 `STOCK_LIST`。

## 指标 Key

指标不直接写死在页面里，而是通过指标注册表暴露。每个指标 key 包含展示名、单位、类型、可用周期和支持的关系。

第一版建议支持：

- 基础行情：`open`、`high`、`low`、`close`、`volume`、`amount`、`pct_chg`
- 实时行情：`current_price`、`change_percent`、`turnover_rate`、`volume_ratio`、`amplitude`
- 均线：`ma5`、`ma10`、`ma20`、`ma30`、`ma60`
- 成交量均线：`volume_ma5`、`volume_ma10`、`volume_ma20`
- 技术指标：`ema12`、`ema26`、`macd_dif`、`macd_dea`、`macd`、`rsi6`、`rsi12`
- 筹码指标：`profit_ratio`（解套率）、`chip_concentration_90`（筹码集中度）、`avg_cost`

主力持仓、估值、行业和新闻情绪类指标可作为二期扩展。

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
- `stock_rule_runs`：规则运行记录，保存状态、目标数量、命中数量、耗时和错误。
- `stock_rule_matches`：规则命中结果，保存股票代码、命中日期、命中条件组、指标快照和解释文本。

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

第二期再做：

- 全市场扫描。
- 定时运行。
- 命中后通知推送。
- 命中后触发问股或生成分析报告。
- 与回测模块联动评估规则有效性。
- 单独重新设计「上穿 / 下穿」。
