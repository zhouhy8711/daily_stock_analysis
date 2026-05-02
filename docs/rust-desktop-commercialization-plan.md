# Rust 本地桌面商业化迁移方案

> 状态：方案文档，尚未实施。
>
> 目标：将当前 Python + React + Electron/PyInstaller 形态，逐步迁移为面向商业分发的三端本地桌面应用。最终交付物应在 Windows、macOS、Linux 上以桌面安装包形式运行，核心逻辑和本地后端以 Rust 原生二进制交付，前端视觉与交互尽量保持现状。

## 1. 核心结论

本项目商业化桌面版建议采用：

- 桌面壳：Tauri 2
- 本地后端：Rust + Axum + Tokio
- 核心引擎：Rust crate
- 本地存储：SQLite，通过 rusqlite 或 sqlx 管理
- 网络访问：桌面端直连第三方行情、搜索、LLM API，不连接项目方自有后台
- 授权方式：离线许可证文件，Ed25519 签名校验
- 前端：继续复用现有 `apps/dsa-web` React/Vite UI，尽量只改 API base URL 注入和桌面环境适配

这个方向的重点不是承诺“绝对无法反编译”。任何发到客户机器上的二进制都存在被逆向的可能。商业保护目标应定义为：

- 不分发 Python 源码、Python 字节码、前端 sourcemap、私钥、测试数据和开发配置。
- 将核心算法、授权校验、报告决策、策略评分等高价值逻辑迁移到 Rust 原生二进制。
- 通过符号裁剪、release 编译、许可证签名、最小化资源暴露，提高逆向和二次分发成本。
- 不把发行方私钥、内部商业 API Key、不可公开的策略参数放入客户端。

## 2. 当前仓库判断

当前项目主要由以下部分组成：

- Python 后端与分析链路：`main.py`、`api/`、`src/`、`data_provider/`、`bot/`
- React/Vite 前端：`apps/dsa-web/`
- Electron 桌面端：`apps/dsa-desktop/`
- 桌面后端打包：现有脚本使用 PyInstaller 生成 `dist/backend/stock_analysis`
- 发布与验证：`scripts/`、`.github/workflows/`、`docker/`

现有 PyInstaller 方案适合快速分发，但不适合作为强商业保护边界。PyInstaller 主要是把 Python 解释器、依赖和字节码打包到一起，仍然可以被提取和分析。因此商业版最终不应依赖 PyInstaller 作为核心保护方式。

## 3. 目标架构

最终商业桌面版结构如下：

```text
Daily Stock Analysis Desktop
├── Tauri shell
│   ├── 创建桌面窗口
│   ├── 加载 React/Vite 构建产物
│   ├── 启动本地 Rust API 服务
│   └── 管理应用数据目录、日志目录和许可证文件
├── React UI
│   ├── 保留现有页面风格和交互
│   ├── 继续调用 /api/v1/*
│   └── 不承载核心商业逻辑
├── Rust local API
│   ├── 监听 127.0.0.1:<random_port>
│   ├── 提供兼容现有前端的 REST API
│   ├── 管理任务、历史、配置、组合、回测、规则
│   └── 统一调用 Rust core / providers / storage / license
├── Rust core
│   ├── 策略评分
│   ├── 风险分析
│   ├── 报告决策
│   ├── 组合分析
│   └── 高价值商业逻辑
├── Rust providers
│   ├── 第三方行情 API
│   ├── 新闻/搜索 API
│   ├── LLM API
│   └── timeout / retry / fallback
├── Rust storage
│   ├── SQLite schema
│   ├── 历史记录
│   ├── 系统配置
│   └── 本地缓存
└── Rust license
    ├── 离线许可证导入
    ├── Ed25519 签名校验
    ├── 设备指纹匹配
    └── 功能 entitlement 判断
```

## 4. Rust workspace 规划

建议新增 Rust workspace，但迁移期不要一次性删除 Python 实现。

推荐模块：

- `crates/dsa-core`
  - 核心分析、策略评分、风险判断、报告决策。
  - 不依赖 Tauri，不依赖 HTTP 框架。
  - 应该是最值得保护、最稳定、测试最充分的内核层。

- `crates/dsa-api`
  - 本地 Axum API 服务。
  - 负责 REST 路由、请求/响应 schema、任务状态、SSE 或轮询兼容。
  - 尽量保持当前前端调用契约不变。

- `crates/dsa-providers`
  - 第三方行情、新闻、搜索、LLM provider。
  - 负责 timeout、retry、fallback、字段标准化。
  - 不内置发行方商业 API Key。

- `crates/dsa-storage`
  - SQLite schema、migration、repository。
  - 保存系统配置、分析历史、任务记录、组合数据、回测结果。

- `crates/dsa-license`
  - 离线许可证解析与验签。
  - 只内置公钥，不内置私钥。
  - 向 core/api 暴露功能开关判断。

- `apps/dsa-desktop/src-tauri`
  - Tauri 入口。
  - 管理窗口、资源路径、本地 API 服务生命周期、数据目录。

## 5. API 兼容策略

为了尽量少改前端，Rust 本地 API 第一阶段应优先复刻前端已经依赖的接口。

必须优先兼容：

- `GET /api/health`
- `POST /api/v1/analysis/analyze`
- `GET /api/v1/analysis/status/{task_id}`
- `GET /api/v1/analysis/tasks`
- `GET /api/v1/analysis/tasks/stream`
- `GET /api/v1/history`
- `GET /api/v1/history/{record_id}`
- `GET /api/v1/history/{record_id}/markdown`
- `GET /api/v1/system/config`
- `POST /api/v1/system/config`
- `GET /api/v1/system/config/schema`
- `POST /api/v1/system/config/validate`

随后迁移：

- 股票行情与指标接口
- 组合账户、交易、现金流水、公司行动接口
- 回测接口
- 规则接口
- Agent/聊天接口
- 图片识别和导入解析接口

新增商业授权接口：

- `GET /api/v1/license/status`
- `POST /api/v1/license/import`
- `POST /api/v1/license/remove`

前端改动原则：

- 保留现有页面结构、组件风格和 API client 分层。
- 只在 API base URL 获取逻辑中增加 Tauri 桌面环境注入。
- 生产构建关闭 sourcemap。
- 不把许可证验签、核心策略、商业参数放到前端。

## 6. 离线许可证设计

许可证建议采用签名文件，不采用本地明文开关。

许可证内容包含：

- license_id
- product_id
- customer_id
- issued_at
- expires_at
- allowed_versions
- enabled_features
- device_fingerprint_hash
- max_local_accounts
- metadata

签名策略：

- 发行方离线或 CI 中使用 Ed25519 私钥签名。
- 应用内只内置 Ed25519 公钥。
- 客户导入许可证文件后，本地验证签名、有效期、产品 ID、版本范围、设备指纹哈希和功能 entitlement。

设备绑定策略：

- 设备指纹只保存哈希，不保存原始硬件信息。
- macOS、Windows、Linux 的设备指纹实现必须分别封装。
- 需要允许客户换机，因此许可证管理流程要预留重新签发机制。

风险边界：

- 纯离线授权无法彻底防盗版。
- 离线授权的目标是防止普通复制和低成本滥用，不是抵抗专业破解团队。
- 若后续需要更强商业保护，应升级为“一次联网激活 + 本地缓存授权”。

## 7. 二进制保护与发布策略

Rust release profile 建议：

- 开启 LTO。
- 设置单 codegen unit。
- strip debug symbols。
- panic 使用 abort。
- 关闭 debug info。

生产包必须禁止：

- `.py`
- `.pyc`
- `.pyo`
- `.map`
- 测试 fixture
- 私钥
- `.env`
- 本地开发配置
- 未裁剪 debug symbol

可选增强：

- Windows 使用代码签名。
- macOS 使用 Developer ID 签名和 notarization。
- Linux 产物附 checksum。
- 商业加壳或混淆只作为后续增强，不作为第一版必需项，以避免杀软误报和稳定性问题。

## 8. 三端构建目标

Windows：

- 目标：x86_64-pc-windows-msvc
- 产物：NSIS 或 MSI
- 要求：代码签名、安装目录权限合理、日志写入用户目录

macOS：

- 目标：aarch64-apple-darwin 和 x86_64-apple-darwin
- 产物：DMG
- 要求：签名、notarization、arm64/x64 分包或 universal 策略明确

Linux：

- 目标：x86_64-unknown-linux-gnu
- 产物：AppImage 和 deb
- 要求：附 checksum，避免依赖过多系统动态库

## 9. 分阶段迁移计划

### Phase 0：冻结契约

目标：在不改业务行为的前提下，建立迁移基准。

动作：

- 梳理现有前端实际调用的 API。
- 为关键接口生成 golden fixtures。
- 明确系统配置、历史记录、分析任务、报告结构的数据契约。
- 补充桌面端商业版构建验收清单。

验收：

- 能清楚列出第一批必须兼容的接口。
- 有固定输入和固定输出作为 Rust 迁移对照。

### Phase 1：Tauri 壳与本地 Rust API 骨架

目标：让桌面应用可以启动 Tauri，并访问本地 Rust API。

动作：

- 新增 Tauri 配置。
- 新增本地 API 服务，先实现 `/api/health` 和许可证状态接口。
- React API client 支持从 Tauri 注入本地 API base URL。
- 保留现有 Electron 构建，直到 Tauri smoke 通过。

验收：

- Windows、macOS、Linux 至少能完成开发模式启动。
- 前端能访问 Rust `/api/health`。
- 不影响当前 Python Web 和 Electron 路径。

### Phase 2：迁移基础数据与配置

目标：Rust 后端具备独立运行的本地配置和 SQLite 存储能力。

动作：

- 实现系统配置读写。
- 实现 SQLite schema/migration。
- 实现历史记录基础读写。
- 实现许可证导入、移除、状态查询。

验收：

- 前端 Settings 页面可在 Rust 后端上完成基础配置读写。
- 许可证状态能控制功能入口。

### Phase 3：迁移分析主链路

目标：将高价值分析链路从 Python 转到 Rust core。

动作：

- 迁移股票代码标准化、行情获取、新闻检索、LLM 调用适配。
- 实现任务创建、任务状态、任务流式进度。
- 实现报告结构输出和历史保存。
- 对照 Python golden fixtures 校验兼容性。

验收：

- 前端首页能触发 Rust 本地分析任务。
- 历史记录页面能查看 Rust 生成的报告。
- 无许可证时限制高级分析能力。

### Phase 4：迁移组合、回测、规则

目标：把商业价值高、复用频繁的功能模块移入 Rust。

动作：

- 迁移组合账户和交易流水。
- 迁移组合风险计算。
- 迁移回测引擎。
- 迁移规则引擎。

验收：

- 组合、回测、规则页面在 Rust 后端下可用。
- 核心计算逻辑不再依赖 Python。

### Phase 5：商业发行收口

目标：商业桌面包不再依赖 Python 后端。

动作：

- 移除商业包中的 PyInstaller 后端。
- 禁止打包 Python 源码和字节码。
- 关闭 sourcemap 和 devtools。
- 补齐三端 CI 构建和产物扫描。
- 补齐签名、notarization、checksum。

验收：

- 三端安装包均可运行。
- 产物扫描通过。
- 核心分析、授权、配置、历史、组合、回测、规则路径都走 Rust。

## 10. 不建议做的事

- 不建议一次性全量重写所有 Python 和 TypeScript。
- 不建议继续把 PyInstaller 当成商业保护核心。
- 不建议把许可证私钥或发行方 API Key 放入客户端。
- 不建议在前端实现商业授权或核心策略。
- 不建议第一版就引入重度加壳，除非已经完成杀软兼容测试。
- 不建议完全离线行情和 LLM，除非接受功能明显降级和本地模型部署复杂度。

## 11. 主要风险

- Rust 重写成本高：当前 Python 数据源生态丰富，尤其是 AkShare、efinance、yfinance、LiteLLM 对 Rust 没有完全等价替代。
- API 兼容风险：前端页面多，接口覆盖广，需要先冻结契约再迁移。
- 三端发行复杂：macOS 签名和 notarization、Windows 签名、Linux 依赖管理都需要单独验证。
- 离线授权强度有限：可以提高普通复制成本，但不能防住专业破解。
- 第三方服务稳定性：桌面端直连第三方 API 时，需要更强的 timeout、retry、fallback 和错误提示。

## 12. 验证清单

迁移过程中每个阶段都应至少验证：

- Rust unit tests
- Rust API contract tests
- Python golden fixture 对照
- React `npm run lint`
- React `npm run build`
- Tauri desktop smoke
- Windows/macOS/Linux 构建 smoke
- 商业包产物扫描

最终商业包产物扫描至少确认：

- 不包含 `.py`
- 不包含 `.pyc`
- 不包含 `.map`
- 不包含私钥
- 不包含 `.env`
- 不包含测试 fixture
- 不包含开发-only 配置
- 不包含未裁剪 debug symbol

## 13. 推荐执行顺序

推荐先做文档和契约，再做骨架，最后迁移核心：

1. 冻结现有前端 API 调用清单。
2. 生成 Python 后端 golden fixtures。
3. 建 Rust workspace 和 Tauri 壳。
4. 实现 `/api/health`、license、system config、history。
5. 迁移分析任务链路。
6. 迁移核心算法和报告决策。
7. 迁移组合、回测、规则。
8. 收口三端商业打包和产物扫描。

第一版商业可交付目标应是：

- 三端桌面包可安装运行。
- UI 基本保持当前风格。
- 核心分析链路运行在 Rust 本地后端。
- 离线许可证可导入和校验。
- 商业包不包含 Python 源码、Python 字节码、sourcemap 和私钥。

