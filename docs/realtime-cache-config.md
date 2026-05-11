# 实时行情缓存配置说明

本文档说明实时行情、全 A 股后台预热、进程内缓存、分钟热表和相关数据源配置。实时 quote 以后台预热快照为第一入口；当日分钟 K 采样会写入 `stock_intraday_minute` 热表，历史日 K 线仍由 SQLite 数据库管理。

## 后台预热行为

API 服务启动后会启动 `RealtimeQuoteCacheWarmer` 后台线程：

- 启动后延迟 5 秒执行第一次全 A 股实时行情预热。
- A 股交易时段（交易日 09:30-11:30、13:00-15:00，上海时间）内默认每 1 分钟执行一次全量刷新；休市、午休、周末或节假日会跳过刷新。
- 股票范围来自 `apps/dsa-web/public/stocks.index.json` 中活跃的 A 股和北交所股票。
- 每轮刷新会生成最新快照，并将 quote 采样写入当日分钟热表。
- 指标分析页切换到分时/分钟周期时会先读分钟热表；如果同周期热表为空，会按同一 `1m/5m/15m/30m/60m` 周期回源拉取分钟 K 并写回热表，不会用日 K 兜底分时。
- 日志按每轮预热输出一条汇总，包含 A 股数量、命中数量、缺失数量、分钟热表写入数量和耗时。

后台预热间隔目前不是环境变量；如果需要调整，需要修改 `src/services/realtime_quote_cache_warmer.py` 中的 `DEFAULT_WARM_INTERVAL_SECONDS`。

## 配置参数

| 参数 | 默认值 | 用处 | 推荐设置 |
| --- | --- | --- | --- |
| `PREFETCH_REALTIME_QUOTES` | `true` | 控制全市场实时行情预取，包括分析任务开始前预取和 API 后台全 A 股缓存预热。 | 保持 `true`。如果部署环境不允许全市场拉取，设为 `false`。 |
| `ENABLE_REALTIME_QUOTE` | `true` | 总开关。关闭后分析和 API 不主动使用实时行情，后台预热也会跳过。 | 保持 `true`。只有想完全退回历史收盘价时设为 `false`。 |
| `REALTIME_QUOTE_CACHE_SECONDS` | `30` | 实时行情快照与短缓存复用秒数。`StockService` 单股 quote 缓存和 efinance/akshare 全市场 DataFrame 缓存都复用它。`0` 表示禁用进程内实时行情缓存。旧名 `REALTIME_CACHE_TTL` 仍兼容读取。 | 常规使用 `30`。如果想让请求路径更久复用快照/短缓存，可设 `60` 或 `120`。不建议小于 `10`。 |
| `REALTIME_SOURCE_PRIORITY` | `tencent,akshare_sina,efinance,akshare_em` | 实时行情数据源优先级，逗号分隔。单股查询按此顺序 fallback；批量缓存补齐优先复用 efinance，再 fallback 到 akshare 批量接口。 | 默认即可。若有 Tushare 高积分账号，可设 `tushare,tencent,akshare_sina,efinance,akshare_em`。 |
| `CIRCUIT_BREAKER_COOLDOWN` | `300` | 实时行情数据源失败后的熔断冷却秒数，避免反复打不可用的数据源。 | 默认 `300`。数据源不稳定时可设 `600`。 |
| `TUSHARE_TOKEN` | 空 | Tushare Pro Token。配置后可用于部分实时/历史数据源能力；若想实时行情优先走 Tushare，还需要调整 `REALTIME_SOURCE_PRIORITY`。 | 没有高积分账号可不配。 |
| `HTTP_PROXY` / `HTTPS_PROXY` | 空 | 网络代理。影响外部行情源请求能否访问。 | 只有本机网络需要代理时设置。 |

## 成交量口径

A 股日 K、分钟热表、实时 quote 快照和规则指标中的 `volume` / `volume_ma*` 统一按「手」保存和展示。部分实时源的原始字段会返回「股」，进入 `UnifiedRealtimeQuote` 前会转换成「手」，避免规则实测结果和指标 K 线图出现 `3.88亿` 与 `3.88万` 这类 100 倍口径偏差。`amount` 始终按「元」保存和展示。

## 常见设置组合

### 默认推荐

```env
PREFETCH_REALTIME_QUOTES=true
ENABLE_REALTIME_QUOTE=true
REALTIME_QUOTE_CACHE_SECONDS=30
REALTIME_SOURCE_PRIORITY=tencent,akshare_sina,efinance,akshare_em
CIRCUIT_BREAKER_COOLDOWN=300
```

效果：A 股交易时段内后台每 1 分钟刷新全 A 股 quote 快照；单股、列表请求和指标大盘优先复用快照/短缓存。

### 降低数据源压力

```env
PREFETCH_REALTIME_QUOTES=true
ENABLE_REALTIME_QUOTE=true
REALTIME_QUOTE_CACHE_SECONDS=120
CIRCUIT_BREAKER_COOLDOWN=600
```

效果：A 股交易时段内后台仍每 1 分钟刷新快照；请求路径在 120 秒内更倾向复用已有快照/短缓存，失败数据源冷却更久。

### 禁用全市场后台预热

```env
PREFETCH_REALTIME_QUOTES=false
ENABLE_REALTIME_QUOTE=true
REALTIME_QUOTE_CACHE_SECONDS=30
```

效果：后台全 A 股预热和分析前批量预取都会跳过；用户请求单股或列表时仍可按需获取实时行情。

### 完全禁用实时行情缓存

```env
ENABLE_REALTIME_QUOTE=true
REALTIME_QUOTE_CACHE_SECONDS=0
```

效果：后台预热跳过；请求实时行情时不命中进程内短缓存，会更频繁访问数据源。一般不推荐。

### 完全禁用实时行情

```env
ENABLE_REALTIME_QUOTE=false
```

效果：后台预热跳过；分析流程退回历史收盘价相关逻辑。

## 运行时说明

- 修改 `.env` 后需要重启 API 进程，后台预热线程才会按新配置启动。
- Web 系统设置页目前可编辑 `REALTIME_QUOTE_CACHE_SECONDS`，并显示当前进程内实时行情缓存内存占用。
- `REALTIME_QUOTE_CACHE_SECONDS` 调大不会改变后台 60 秒预热频率，只会延长请求路径对进程内 quote 和全市场 DataFrame 的复用时间；休市时后台预热不会刷新新快照。
- 默认 quote 请求顺序为：最新预热快照、短缓存、远程实时源；`snapshot_only`、`cache_only` 和 `db_only` 不会触发远程实时行情请求。
- 指标大盘的 1m / 分时 K 线优先读 `stock_intraday_minute`；`cache_only` 缺热表数据时返回空结果，默认策略或页面按需预热会回源拉取同周期分钟 K 并写回热表，不会降级展示日 K。

## 历史日线与筹码峰补齐

可用 `tools/backfill_a_share_daily_history.py` 批量补齐全部活跃 A 股在指定时间范围内的历史日线，并写入 SQLite `stock_daily` 表；脚本默认也会基于含换手率的日 K 数据计算每日筹码峰快照并写入 `stock_chip_daily` 表：

```bash
python tools/backfill_a_share_daily_history.py --start-date 2025-01-01 --end-date 2025-12-31
python tools/backfill_a_share_daily_history.py --start-date 2025-01-01 --end-date 2025-12-31 --parallelism 20
python tools/backfill_a_share_daily_history.py --start-date 2025-01-01 --end-date 2025-12-31 --fetcher baostock
python tools/backfill_a_share_daily_history.py --start-date 2025-01-01 --end-date 2025-12-31 --skip-chip
```

脚本会先读取 A 股交易日历，再按 `stock_daily(code,date)` 判断每只股票在目标交易日内已有的数据；只有缺失的交易日会被合并成连续区间回源拉取，已有日期不会重复写入。`--parallelism` 控制并发抓取股票数，默认 `10`。股票范围默认来自 `stocks.index.json` 中活跃 A 股，也可通过 `--codes 600519,000001` 做小范围补齐。默认 `--fetcher manager` 使用系统数据源 fallback 链；如果当前网络下东方财富类接口不可用，可用 `--fetcher baostock` 直连 Baostock 补数，避免继续探测不适用的兜底源。

筹码峰补齐会先检查 `stock_chip_daily(code,date)`，只为缺失的交易日回源读取更长窗口的日 K 数据并计算本地筹码模型。`--skip-chip` 可只补 `stock_daily`。正常分析流水线如果已经成功取得筹码分布，也会把返回的交易日快照写入 `stock_chip_daily`，后续回测命中弹窗即可直接按命中日读取 DB，不需要在用户点击时访问外部 HTTP 数据源。

## 数据诊断

后台提供只读诊断接口查看历史库、当天分钟热表和实时 quote 缓存状态：

```bash
curl "http://127.0.0.1:8000/api/v1/diagnostics/stock-data?scope=observed&limit=50"
```

常用参数：

- `trade_date=YYYY-MM-DD`：查看指定日期的分钟热表，默认当天。
- `scope=observed|history_db|active_a_share`：默认 `observed`，表示历史库、分钟热表或 quote 缓存中出现过的股票。
- `q=600519`：按股票代码或名称过滤。
- `sort=code|history_rows_desc|intraday_rows_desc|latest_daily_desc`：控制明细排序。

也可以使用 HTTP smoke 脚本：

```bash
python tools/check_stock_data_diagnostics.py --base-url http://127.0.0.1:8000 --limit 50
python tools/check_stock_data_diagnostics.py --q 688521 --json
```

该接口和脚本只读，不会触发远程行情请求，不会补数据或清理缓存。
