# 实时行情缓存配置说明

本文档说明实时行情、全 A 股后台预热、进程内缓存和相关数据源配置。实时行情只放在进程内缓存中；历史日 K 线仍由 SQLite 数据库管理。

## 后台预热行为

API 服务启动后会启动 `RealtimeQuoteCacheWarmer` 后台线程：

- 启动后延迟 5 秒执行第一次全 A 股实时行情缓存补齐。
- 之后默认每 1 分钟执行一次补齐。
- 股票范围来自 `apps/dsa-web/public/stocks.index.json` 中活跃的 A 股和北交所股票。
- 每轮只补当前 `REALTIME_CACHE_TTL` 缓存桶里缺失的股票 quote。
- 日志按每轮预热输出一条汇总，包含 A 股数量、已缓存数量、补齐数量、失败数量和耗时。

后台预热间隔目前不是环境变量；如果需要调整，需要修改 `src/services/realtime_quote_cache_warmer.py` 中的 `DEFAULT_WARM_INTERVAL_SECONDS`。

## 配置参数

| 参数 | 默认值 | 用处 | 推荐设置 |
| --- | --- | --- | --- |
| `PREFETCH_REALTIME_QUOTES` | `true` | 控制全市场实时行情预取，包括分析任务开始前预取和 API 后台全 A 股缓存预热。 | 保持 `true`。如果部署环境不允许全市场拉取，设为 `false`。 |
| `ENABLE_REALTIME_QUOTE` | `true` | 总开关。关闭后分析和 API 不主动使用实时行情，后台预热也会跳过。 | 保持 `true`。只有想完全退回历史收盘价时设为 `false`。 |
| `REALTIME_CACHE_TTL` | `30` | 实时行情进程内缓存秒数。`StockService` 单股 quote 缓存和 efinance/akshare 全市场 DataFrame 缓存都复用它。`0` 表示禁用进程内实时行情缓存。 | 常规使用 `30`。想降低数据源压力可设 `60` 或 `120`。不建议小于 `10`。 |
| `REALTIME_SOURCE_PRIORITY` | `tencent,akshare_sina,efinance,akshare_em` | 实时行情数据源优先级，逗号分隔。单股查询按此顺序 fallback；批量缓存补齐优先复用 efinance，再 fallback 到 akshare 批量接口。 | 默认即可。若有 Tushare 高积分账号，可设 `tushare,tencent,akshare_sina,efinance,akshare_em`。 |
| `CIRCUIT_BREAKER_COOLDOWN` | `300` | 实时行情数据源失败后的熔断冷却秒数，避免反复打不可用的数据源。 | 默认 `300`。数据源不稳定时可设 `600`。 |
| `TUSHARE_TOKEN` | 空 | Tushare Pro Token。配置后可用于部分实时/历史数据源能力；若想实时行情优先走 Tushare，还需要调整 `REALTIME_SOURCE_PRIORITY`。 | 没有高积分账号可不配。 |
| `HTTP_PROXY` / `HTTPS_PROXY` | 空 | 网络代理。影响外部行情源请求能否访问。 | 只有本机网络需要代理时设置。 |

## 常见设置组合

### 默认推荐

```env
PREFETCH_REALTIME_QUOTES=true
ENABLE_REALTIME_QUOTE=true
REALTIME_CACHE_TTL=30
REALTIME_SOURCE_PRIORITY=tencent,akshare_sina,efinance,akshare_em
CIRCUIT_BREAKER_COOLDOWN=300
```

效果：后台每 1 分钟补齐全 A 股当前缓存桶缺失 quote；单股和列表请求优先复用缓存。

### 降低数据源压力

```env
PREFETCH_REALTIME_QUOTES=true
ENABLE_REALTIME_QUOTE=true
REALTIME_CACHE_TTL=120
CIRCUIT_BREAKER_COOLDOWN=600
```

效果：后台仍每 1 分钟检查，但 120 秒内已有缓存的股票不会重复补齐；失败数据源冷却更久。

### 禁用全市场后台预热

```env
PREFETCH_REALTIME_QUOTES=false
ENABLE_REALTIME_QUOTE=true
REALTIME_CACHE_TTL=30
```

效果：后台全 A 股预热和分析前批量预取都会跳过；用户请求单股或列表时仍可按需获取实时行情。

### 完全禁用实时行情缓存

```env
ENABLE_REALTIME_QUOTE=true
REALTIME_CACHE_TTL=0
```

效果：后台预热跳过；请求实时行情时不命中进程内短缓存，会更频繁访问数据源。一般不推荐。

### 完全禁用实时行情

```env
ENABLE_REALTIME_QUOTE=false
```

效果：后台预热跳过；分析流程退回历史收盘价相关逻辑。

## 运行时说明

- 修改 `.env` 后需要重启 API 进程，后台预热线程才会按新配置启动。
- Web 系统设置页目前可编辑 `REALTIME_CACHE_TTL`，并显示当前进程内实时行情缓存内存占用。
- `REALTIME_CACHE_TTL` 调大不会写入数据库，只会延长当前进程内 quote 和全市场 DataFrame 的可复用时间。
- 如果当前缓存桶里缺少某只股票，请求路径会自动补齐这只股票；后台预热下一轮也会补齐全 A 股缺口。
