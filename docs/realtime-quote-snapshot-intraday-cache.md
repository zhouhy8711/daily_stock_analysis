# 实时行情快照与分钟热表落地方案

## 背景

实测页按固定周期触发规则扫描时，如果每轮都直接访问实时行情源，上一轮执行慢于下一次触发间隔时就会出现跳过、堆积或旧结果覆盖新摘要的问题。指标分析页的 quote、今日 K 线增强和 1m/分时 K 线如果各自回源，也会让同一批实时行情数据被重复拉取。

本方案约定一条统一数据入口：后台预热负责刷新全量 A 股实时 quote 快照，请求路径优先读取快照和本地热表，只有默认策略下确实缺数据时才远程补数。

## 配置命名

`REALTIME_CACHE_TTL` 改名为 `REALTIME_QUOTE_CACHE_SECONDS`。新名字表达的是“实时 quote 快照与短缓存复用窗口”，不是历史数据是否过期。

- 不新增第二个控制参数。
- 旧名 `REALTIME_CACHE_TTL` 仅作为兼容别名读取。
- 新旧同时存在时，`REALTIME_QUOTE_CACHE_SECONDS` 优先。
- Web 系统设置页只展示新名。

## 预热快照

`RealtimeQuoteCacheWarmer` 在 A 股交易时段（交易日 09:30-11:30、13:00-15:00，上海时间）默认每 60 秒执行一次，休市时跳过刷新；股票范围来自 `apps/dsa-web/public/stocks.index.json` 中活跃 A 股和北交所股票。

每轮预热产出最新快照：

- `snapshot_id`：按快照时间生成，格式 `YYYYMMDDHHMMSS`。
- `snapshot_time`：快照生成时间。
- `items_by_code`：按标准股票代码索引的 quote payload。
- `snapshot_hit_count` / `snapshot_miss_count`：本轮成功和缺失统计。

quote 查询顺序：

1. 最新预热快照。
2. 当前进程内短缓存。
3. 默认策略下远程实时行情源。

`snapshot_only` 只读快照和本地历史，不触发实时行情远程请求；实测页使用该策略。`cache_only` 可读快照、短缓存和本地历史/热表，也不触发远程实时行情请求。

## 分钟热表

新增 `stock_intraday_minute` 表保存当天分钟级采样数据。后台每轮预热会把 quote 样本写入热表。

字段要点：

- `code`、`trade_date`、`minute_ts`：唯一键定位股票和分钟。
- `open`、`high`、`low`、`close`：同一分钟多次采样时 upsert 聚合。
- `volume`、`amount`：优先按累计成交量/成交额差值估算分钟增量。
- `turnover_rate`、`change_percent`、`source`：保留实时 quote 附带指标和来源。
- `snapshot_id`、`snapshot_time`：记录该分钟最后一次采样来自哪轮快照。

指标分析页的 `1m` 和分时图优先读取该热表。`cache_only` 缺少分钟热表数据时返回空结果和 `intraday_hot_table_miss` 来源；页面按需预热或默认策略会继续按同一分钟周期回源拉取分钟 K，并写回 `stock_intraday_minute`。分时/分钟周期严禁降级展示日 K。港股和美股分钟热表后续单独扩展。

## 收盘归档

新增从分钟热表聚合到 `stock_daily` 的归档能力：

- `open`：首个分钟 open。
- `high` / `low`：全日最高/最低。
- `close`：最后一个分钟 close。
- `volume` / `amount`：分钟增量求和。
- `pct_chg`：按当日 open 到 close 估算。

分钟热表默认保留近 3 天，避免无限增长；收盘归档后可定期清理更早热表数据。

FastAPI/Web 服务启动和 `python main.py --schedule` 定时模式都会注册收盘归档后台任务。任务每 30 分钟扫描一次；当 A 股市场本地时间达到 16:00 后，会把当天 `stock_intraday_minute` 中的分钟数据按股票聚合并 upsert 到 `stock_daily`，确认对应日线行存在后再删除当天已归档的分钟热表数据。单只股票归档失败时保留该股票热表数据，等待下一轮重试。

## 前端约定

实测页：

- 每轮请求携带 `dataPolicy: snapshot_only`。
- 只在 A 股交易时段触发；休市、午休、周末或节假日不再按旧快照重复扫描。
- 最多允许 2 个实测 cycle 并发。
- 结果按 `snapshot_id` 去重；旧快照结果不会覆盖更新快照的摘要。

指标分析页：

- quote 请求携带 `cache_only`。
- 日 K、1m、分时和其他分钟周期先请求 `cache_only`。
- 分时/分钟周期收到 `intraday_hot_table_miss` 时，会用同一周期发起一次默认策略请求来完成按需预热，成功后仍从分钟热表渲染。
- K 线图展示数据来源和快照时间，便于确认 1m/分时是否来自分钟热表。

## 验证重点

- `REALTIME_QUOTE_CACHE_SECONDS` 与旧名兼容解析，新名优先。
- `snapshot_only` 不调用远程 quote。
- 后台预热使用全量活跃 A 股代码并刷新快照。
- 分钟热表同一分钟 upsert 聚合正确。
- 指标分析 history 接口优先返回分钟热表。
- 分时/分钟热表 miss 后只回源同周期分钟 K，不回退日 K。
- 收盘归档能 upsert 到 `stock_daily`。
