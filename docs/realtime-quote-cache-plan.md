# 实时行情缓存落地方案

## 目标

API 服务启动后在后台静默预热全量 A 股实时行情缓存，Web 首页进入自选或“A股所有”时避免同一时间窗口内重复请求实时行情接口，并减少列表从 `--` 到价格刷出的空窗。缓存只覆盖实时 quote，不把一年级别历史 K 线放入长期内存缓存。

## 分层策略

1. 后端 `StockService` 维护进程内短缓存，key 为 `bucket_start + normalized_stock_code`。`bucket_start = floor(now / REALTIME_CACHE_TTL) * REALTIME_CACHE_TTL`，表示当前 TTL 时间桶起点。
2. 后端只保留当前 bucket。进入新 bucket 时清空旧 quote，避免旧实时行情长期驻留。
3. `REALTIME_CACHE_TTL=0` 时禁用后端进程内实时 quote 缓存。
4. `efinance_fetcher.py` 与 `akshare_fetcher.py` 的全市场实时 DataFrame 缓存统一读取 `get_config().realtime_cache_ttl`，不再硬编码 600/1200 秒。
5. efinance/akshare 的全市场实时 DataFrame 刷新需要在缓存锁内执行；TTL 过期时并发请求只允许一个线程回源，其余线程等待后复用同一份刷新结果。
6. API 生命周期启动 `RealtimeQuoteCacheWarmer` 后台线程，默认每 1 分钟从 `stocks.index.json` 读取全量活跃 A 股代码并补齐当前缓存桶缺失 quote。`PREFETCH_REALTIME_QUOTES=false`、`ENABLE_REALTIME_QUOTE=false` 或 `REALTIME_CACHE_TTL=0` 时跳过后台预热。
7. 请求路径仍会自动补缺：当读取时发现当前 `REALTIME_CACHE_TTL` bucket 内缺少目标股票，会复用同一批量接口补齐缺口并写回缓存。
8. Web 首页自选与“A股所有”共用前端短缓存。切换 Tab 或刷新时保留已展示价格，只对缺失或过期 quote 发起后台补齐。
9. 历史日线继续优先读取 `stock_daily` SQLite。DB 缺失时才拉取外部日线并 upsert，不引入 Redis、磁盘新缓存或长期内存 K 线缓存。
10. Web 系统设置页展示 `REALTIME_CACHE_TTL`，字段右侧显示当前 Python 进程内实时行情缓存占用，单位 MB。

## 配置项

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| `PREFETCH_REALTIME_QUOTES` | `true` | 控制分析前预取和 API 后台全 A 股实时缓存预热；设为 `false` 时跳过全市场预取 |
| `REALTIME_CACHE_TTL` | `30` | 实时行情短缓存秒数；按时间桶缓存单股 quote 与全市场实时 DataFrame |
| `REALTIME_CACHE_TTL=0` | - | 禁用进程内实时 quote / 全市场实时 DataFrame 命中缓存 |

## 内存估算

实时 quote payload 只包含当前价、涨跌幅、成交量、金额、市值、估值、股本、涨跌停等几十个字段。按 Python dict/object 开销粗估每只约 1-2 KB，6000 只股票约 6-12 MB；即使考虑重复字符串与锁开销，目标仍控制在十几 MB 级别。

不把一年历史日线放入内存的原因：

| 方案 | 单股一年估算 | 6000 只估算 | 结论 |
| --- | ---: | ---: | --- |
| Python list/dict K 线 | 约 100-120 KB | 约 600-720 MB | 长驻内存过高 |
| pandas DataFrame K 线 | 约 25-35 KB | 约 150-210 MB | 仍不适合长期常驻 |
| SQLite `stock_daily` | 磁盘存储，按需读取 | 由 DB 管理 | 采用此方案 |

## 测试证据格式

交付时记录以下命令结果：

```bash
python -m pytest tests/test_stock_indicator_metrics.py tests/test_get_latest_data.py tests/test_config_env_compat.py
cd apps/dsa-web && npm run test -- HomePage.test.tsx
./scripts/ci_gate.sh
cd apps/dsa-web && npm run lint && npm run build
```

测试覆盖点：

- 同 bucket 命中缓存、跨 bucket 重新拉取、旧 bucket 清理、TTL 为 0 禁用缓存。
- 批量 quote 请求只补缺失股票。
- 后台全 A 股预热从股票索引读取活跃 A 股代码，并只补当前缓存桶缺失 quote。
- 数据源全市场缓存 TTL 从配置读取。
- 全市场实时 DataFrame 缓存并发 miss 会合并为一次回源刷新。
- DB 已有日线时不触发外部日线 fetch，缺失时 fetch 后写入 `stock_daily`。
- 实时 quote 不写入长期历史缓存。
- Web 首页自选和“A股所有”共用 quote 短缓存，刷新时旧价格保持到新数据返回。
- Web 系统设置页可编辑 `REALTIME_CACHE_TTL`，并显示实时行情缓存内存占用。
