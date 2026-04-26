# Bloomberg、LSEG/Refinitiv、Reuters、Wind 竞品调研与项目借鉴路线

调研日期：2026-04-26

## 结论摘要

当前项目已经具备 A 股、港股、美股的多数据源行情、新闻搜索、LLM 报告、Web 工作台、自选监控、历史回看、指标分析和多渠道通知能力。对标 Bloomberg、LSEG/Refinitiv、Reuters、Wind 后，最值得吸收的不是“复制一个昂贵终端”，而是把现有能力升级为更可信、更可追溯、更工作流化的投研助手。

优先级最高的借鉴方向有五类：

1. 数据可信度治理：记录每个行情、公告、新闻、财务字段来自哪里、何时抓取、是否兜底、是否缺字段，并在报告中暴露来源可信度。
2. 结构化事件流：把新闻、公告、财报、资金流、评级变动、异动行情统一成可排序、可去重、可引用的事件时间线。
3. 新闻与舆情增强：学习 Reuters / LSEG 的“快、准、可追溯”逻辑，做新闻去重、来源分层、发布时间过滤、摘要引用和冲突提示。
4. 工作台体验：学习 Bloomberg / Wind 的一体化工作流，把行情、指标、报告、新闻、历史观点、告警和导出放在同一个决策闭环里。
5. A 股本土化深度：学习 Wind 对公告、研报、板块、指数、宏观、产业链、基金债券等本土数据的深覆盖，但优先从公开合法数据做轻量版。

不建议照搬的方向：交易执行系统、机构 IM 网络、专有实时全量行情 feed、付费研报原文聚合、复杂企业级权限计费系统。这些能力授权成本高、合规边界重，也偏离当前开源项目的个人/小团队定位。

## 角色关系先校正

用户给出的四个对象里，有两个容易混淆：

- Thomson Reuters 现在主要是法律、税务、会计、合规、政府和媒体业务的信息服务公司。其新闻业务 Reuters 仍属于 Thomson Reuters。
- Refinitiv / Eikon / Workspace 这条金融数据终端线已经并入 LSEG。Thomson Reuters 在 2021 年宣布完成将 Refinitiv 出售给 London Stock Exchange Group 的交易；LSEG 官方也说明 Refinitiv 不再作为独立品牌运营，Eikon 已由 LSEG Workspace 替代。
- Reuters 是新闻社，不是金融终端。它的强项是全球新闻采编、授权供稿、实时新闻分发和新闻可信度治理。
- Wind 是中国本土金融数据终端与数据服务商，核心优势在 A 股、中资债、公告、宏观行业、指数、研报和本地机构工作流。

因此，本报告把 Thomson Reuters / Reuters / LSEG Workspace 拆开分析：Thomson Reuters 提供“专业知识库与可信内容工程”的借鉴，Reuters 提供“新闻生产与授权分发”的借鉴，LSEG Workspace 提供“金融数据终端与数据分析工作流”的借鉴。

## 总览对比

| 对象 | 当前核心定位 | 主要用户 | 长处关键词 | 对当前项目最有价值的启发 |
| --- | --- | --- | --- | --- |
| Bloomberg | 机构级金融终端、企业数据、新闻、交易与工作流网络 | 投行、资管、对冲基金、交易台、企业财资 | 实时数据、跨资产覆盖、数据标准化、终端工作流、消息网络、交易能力 | 做“可信数据 + 决策工作台”，重点学数据血缘、事件流、低摩擦工作流，不学交易执行 |
| LSEG / Refinitiv | LSEG Workspace、数据与分析、Reuters 新闻接入、API 与 Office 工作流 | 投行、资管、研究员、组合经理、企业 | Reuters 新闻、Workspace、CodeBook、Office 集成、AI 摘要、StarMine/MarketPsych 等分析 | 做“数据 + 新闻 + AI 摘要 + 引用”的结构化投研链路 |
| Thomson Reuters | 法律、税务、会计、合规、政府、媒体信息服务；Reuters 新闻母公司 | 律所、会计师、企业法务、税务、媒体 | 权威知识库、专业工作流、可信内容、AI 辅助专业服务 | 借鉴“专业内容库 + AI 但保留权威来源/审计线索”的设计 |
| Reuters | 全球新闻通讯社与 B2B 内容授权平台 | 媒体、平台、政府、企业、数据终端 | 突发新闻、全球采编、Trust Principles、授权供稿、AI 辅助但编辑把关 | 建立新闻来源分层、去重、时间戳、引用、冲突检测与可信摘要 |
| Wind | 中国机构金融终端、数据库服务、指数、EDB、研报和本土金融工作流 | 国内券商、基金、银行、保险、监管、研究机构、高校 | A 股/债券/基金/宏观行业、公告资讯、研报、Excel/API、本土化 | 做 A 股深度增强：公告/财报/板块/产业/宏观/资金/指数上下文 |

## 价格与授权观察

价格只能作为公开估算，不应写死到产品逻辑或文档宣传中：

- Bloomberg Terminal 官方通常需要销售报价。公开报道和第三方估算常见区间约为每席每年 2.4 万到 3.2 万美元，实际取决于合同、席位数、交易所授权和附加模块。
- Refinitiv Eikon / LSEG Workspace 官方同样以销售报价为主。第三方估算常见区间跨度很大，轻量方案和机构桌面版可能从数千美元到两万美元以上不等。
- Wind 官方公开页面不直接给标准价。国内采购公示显示其价格强依赖账号数量、模块、机构类型和服务范围。例如深圳市地方金融监督管理局 2020 年 Wind 资讯金融终端服务项目使用期一年、费用合计 13 万元；四川银行 2024 至 2025 年 Wind 金融终端采购项目包含 35 个 WFT/EDB/实时行情账号和 2 个全球企业库 VIP 账号，最高限价为 200.7 万元。

对本项目的含义：不要把“便宜版 Bloomberg/Wind”作为核心叙事。真正可持续的定位应是“开源、可自托管、可接多种公开/授权数据源、可解释的 AI 投研工作台”。

## Bloomberg：最值得学的是数据血缘与工作流闭环

### 核心优势

Bloomberg 的强项不是单一界面，而是“数据、新闻、分析、通信、交易”被整合到一个高频工作流里。

关键能力：

- 实时市场数据覆盖广。Bloomberg B-PIPE 官方介绍其面向企业提供标准化实时行情，覆盖与 Bloomberg Terminal 相同资产类别，并提到 3500 万金融工具、330 多个交易所、5000 多个贡献者等覆盖规模。
- 数据标准化能力强。B-PIPE 不只是转发行情，还会处理交易状态、交易类型、tick size、交易所权限等标准化问题，这对机构前台、风控和合规非常关键。
- 企业数据产品完整。Bloomberg Data License 覆盖参考数据、ESG、定价、风险、监管、基本面、预期、历史数据等，并支持 REST API、SFTP 和云端交付。
- 事件数据方向明确。Bloomberg 2025 年发布 Real-Time Events Data，方向是把市场数据、定价、事件、新闻洞察和分析放到统一实时管道里。
- 终端工作流成熟。Terminal 的价值来自“查得快、联动快、可操作”，包括命令体系、图表、新闻、监控、Excel/API、消息和交易入口。

### 可借鉴到本项目的点

1. 数据来源血缘
   - 当前项目已有多数据源 fallback，但用户看报告时不一定知道关键字段来自哪个源。
   - 应扩展为每条行情、财务、资金流、新闻结果都带 `source/provider/captured_at/published_at/fallback_path/field_completeness`。
   - 报告中显示“本次数据可信度摘要”：例如实时价来自 Longbridge，K 线来自 YFinance，新闻来自东方财富和 Tavily，财务字段部分缺失。

2. 数据质量评分
   - Bloomberg 的优势之一是 normalized data。当前项目可做轻量版：按新鲜度、字段完整度、来源优先级、是否兜底、是否跨源一致，计算 `data_quality_score`。
   - 如果价格、涨跌幅、成交量在多个源之间偏差过大，报告应提示“数据源冲突”，而不是静默取一个值。

3. 事件流
   - Bloomberg 的 Real-Time Events Data 说明机构用户需要从碎片化披露里抽取事件。
   - 当前项目应把“新闻搜索结果”升级为“结构化事件”：公告、财报、业绩预告、分红、回购、减持、监管处罚、评级变动、异动行情、资金流异常。
   - LLM 报告不应只读一堆搜索摘要，而应读按时间排序的事件时间线。

4. 工作台闭环
   - Bloomberg 的真正壁垒是用户不用离开终端。
   - 当前 Web 已有首页自选监控、报告浮窗和指标分析弹窗，应继续往“同屏决策”扩展：左侧自选和告警，中间行情/指标，右侧事件和报告，底部历史观点与回测结果。

### 不建议照搬

- 不做交易执行和订单路由。
- 不做机构消息网络。
- 不做专有实时全市场 tick feed 的再分发。
- 不承诺 Bloomberg 级实时性和数据授权覆盖。

## LSEG / Refinitiv：最值得学的是“新闻 + 数据 + AI 摘要”的组合

### 核心优势

LSEG Workspace 是 Refinitiv/Eikon 的继任产品。LSEG 官方说明 Refinitiv 品牌已退役，Eikon 已由 LSEG Workspace 替代。

关键能力：

- Workspace 把金融数据、Reuters 新闻、分析工具和工作流放在一起。
- LSEG 官方介绍 Workspace 支持 CodeBook，即集成 Python/Jupyter 的脚本环境，并通过 LSEG APIs 和数据驱动分析工作流。
- Workspace 支持 Microsoft Office 集成，可在 Excel 建模，并更新 Word/PowerPoint 展示。
- LSEG 与 Microsoft 合作强化 Teams、Microsoft 365 和合规工作流互操作。
- Reuters Super Summaries 是一个值得重点关注的方向：它将 Reuters 新闻、LSEG 市场数据和分析师预期结合，把财报事件生成结构化摘要，并由 Reuters 记者编辑把关。LSEG 页面提到初期覆盖 3500 家美国和加拿大公司，后续扩展到最多 10000 家全球公司。

### 可借鉴到本项目的点

1. AI 摘要必须绑定数据上下文
   - 当前项目已经有 LLM 报告，但可进一步要求每个“重大结论”引用具体事件、指标和来源。
   - 类似 Super Summaries 的格式可简化为：标题、事件概述、关键数字、驱动因素、市场反应、风险提示、来源列表。

2. 摘要结构固定化
   - 财报、公告、新闻、资金流、技术形态应使用不同摘要模板。
   - 例如财报摘要固定输出：营收、利润、现金流、毛利率、管理层展望、市场预期差、股价反应。
   - 公告摘要固定输出：事件类型、影响对象、金额/比例、时间节点、历史可比、潜在风险。

3. 分析环境开放
   - LSEG 的 CodeBook / API 思路说明专业用户需要在终端内做二次计算。
   - 当前项目可优先做轻量导出：报告 Markdown、事件 CSV、行情/指标 CSV、回测结果 CSV。
   - 后续可开放本地 API 文档和示例 Notebook，而不是一开始内置复杂 notebook 环境。

4. Office / Excel 工作流
   - 金融用户大量决策仍发生在 Excel 里。
   - 当前项目可新增“复制为表格 / 导出 CSV / 下载分析包”，让用户把 AI 结论、关键指标、事件时间线带到自己的表格模型。

### 不建议照搬

- 不引入复杂企业 Office 插件体系作为早期目标。
- 不做 LSEG/Reuters 授权内容的抓取或再分发。
- 不宣称拥有 StarMine、MarketPsych 等专有模型能力。

## Thomson Reuters：最值得学的是专业内容治理

### 核心优势

Thomson Reuters 当前不是 Refinitiv 金融终端的运营主体。它的主要业务覆盖法律、税务、会计、合规、政府和媒体。官方年报介绍称，公司服务 legal、tax、accounting、compliance、government、media 等专业人群；Reuters 仍是 Thomson Reuters 的一部分。

关键能力：

- 面向专业人群构建高可信知识库，而不是泛搜索结果。
- 软件和内容深度结合，例如法律、税务、会计工作流中，知识来源、版本、引用、审计痕迹非常重要。
- AI 能力建立在专业内容和流程约束之上，而不是让模型自由发挥。

### 可借鉴到本项目的点

1. 把“搜索结果”变成“可审计资料库”
   - 当前项目应保存每次报告使用过的新闻、公告、数据快照和来源链接。
   - 用户回看历史报告时，应能看到当时的资料上下文，而不是只看到 LLM 最终文字。

2. 报告结论分级
   - 学专业知识库的方式，把结论拆成“事实、推断、建议、风险”。
   - 事实必须有来源；推断必须说明依据；建议必须给条件；风险必须给触发信号。

3. 版本与口径管理
   - 财务指标、估值口径、技术指标参数、新闻窗口都应随报告保存。
   - 这样回测时才能知道当时用的是 3 天新闻窗口、MA5/MA10/MA20，还是更长周期。

### 不建议照搬

- 不做法律/税务/合规专业数据库。
- 不把项目定位扩展到泛专业服务平台。

## Reuters：最值得学的是新闻可信度和分发标准

### 核心优势

Reuters 是全球新闻通讯社。Reuters 官方页面展示其有 2600 名记者、覆盖 165 个国家、12 种语言，并强调实时、客观、可信新闻。Reuters Connect 则是内容授权平台，提供 Reuters 内容及 100 多个媒体品牌，归档可追溯到 1896 年。

关键能力：

- 全球突发新闻网络，速度和覆盖面强。
- Trust Principles 强调 integrity、independence、unbiased reporting。
- B2B 授权分发成熟，可通过平台、API 和集成方式进入媒体和金融终端。
- 在 AI 新闻摘要上强调编辑把关和透明披露。

### 可借鉴到本项目的点

1. 新闻来源分层
   - 建议建立 `source_tier`：官方公告/交易所/监管 > 权威财经媒体 > 主流门户 > 社交平台/论坛。
   - 不同层级在报告里权重不同。重大结论尽量由高层级来源支撑。

2. 新闻去重与事件合并
   - 同一公告会被多个媒体转载，当前项目应按标题、主体、时间、关键词和 URL canonical 合并。
   - 报告只展示事件本身，不堆重复新闻。

3. 时效性和冲突检测
   - 当前已有新闻最大时效配置，可进一步展示 `published_at` 和 `age_hours`。
   - 如果同一事件存在相互矛盾的报道，报告应提示“来源冲突”，并列出来源。

4. 摘要透明
   - AI 摘要应标注“模型生成”，并列出来源。
   - 对于高风险结论，如业绩变脸、监管处罚、重大诉讼，应优先引用公告或监管来源，不能只引用二级媒体。

### 不建议照搬

- 不抓取 Reuters 付费授权内容。
- 不在没有编辑团队的情况下宣称“新闻级客观中立”。
- 不把社交舆情和新闻通讯社内容混同为同等可信。

## Wind：最值得学的是 A 股本土化深度

### 核心优势

Wind 是中国金融机构常用的数据终端和数据库服务。Wind 官网描述其金融终端服务中国金融市场超过 20 年，覆盖全球金融市场数据与信息；其数据库服务覆盖 A 股、B 股、期货、债券、基金、新三板、港股、资管、理财、宏观行业、公告资讯等，支持 SQL Server、Oracle、MySQL 等格式和系统接口定制。

关键能力：

- A 股和中资资产本土数据深，包括股票、基金、债券、指数、宏观行业、公告资讯、研报等。
- Wind 金融终端官方页面介绍其指数数据覆盖中国及全球超过 100 个市场，并包含主流指数公司的数据。
- 宏观行业数据丰富，页面提到超过 800 万个指标、2000 个模板、21 个大类行业的深度行业指标。
- 新闻与研究报告能力强，页面提到实时跟踪 180 多个财经媒体、200 多个行业网站，并有近 50 家证券或行业研究机构的官方授权研报发布平台。
- Excel 插件和 Client API 是机构用户高频工作流入口。
- 数据服务支持 FileSync 自动下载更新入库，强调数据时效性、稳定性和标准化结构。

### 可借鉴到本项目的点

1. A 股专题深度
   - 当前项目应优先补强中国市场特有数据：公告、业绩预告、龙虎榜、融资融券、北向资金、板块概念、产业链、限售解禁、股东增减持、监管问询。
   - 不需要一次做全，但要把这些信息纳入统一事件模型。

2. 宏观和行业上下文
   - 单股分析不能只看个股新闻。
   - 对 A 股，至少要补“所属行业/概念板块表现、指数环境、成交热度、政策/产业事件”。
   - 对美股/港股，补“指数/行业 ETF/同业公司/汇率利率背景”。

3. 研报思路，但不抓付费研报
   - Wind 的研报平台价值很大，但版权边界明确。
   - 当前项目可借鉴研报结构：投资逻辑、催化剂、风险、估值、同业比较；数据来自公开财报、公告和合法 API。

4. Excel/API
   - Wind 用户离不开 Excel 和 API。
   - 当前项目短期可做 CSV/Excel 导出和 API 示例；中期再考虑更完整的数据包导出。

5. 数据同步和本地库
   - Wind FileSync 的启发是：关键数据应可本地缓存、增量更新、可追溯。
   - 当前项目已有 SQLite 和数据源 fallback，可继续扩展“增量事件库”和“数据源健康状态表”。

### 不建议照搬

- 不聚合付费研报全文。
- 不做未经授权的 Wind 数据兼容层。
- 不追求覆盖所有金融品种，应先围绕股票分析主流程扩展。

## 当前项目应扩充的能力清单

### P0：马上值得做，投入小、收益大

1. 数据来源与质量卡片
   - 每次报告保存 `market_data_sources`、`news_sources`、`fundamental_sources`。
   - 展示字段：来源、抓取时间、发布时间、是否 fallback、字段缺失、跨源冲突。
   - 报告顶部给出“数据质量：高/中/低”和原因。

2. 新闻/公告事件时间线
   - 把新闻搜索结果、公网公告、财报摘要、资金流异动统一为事件列表。
   - 每个事件包含：代码、市场、主体、事件类型、标题、摘要、来源、URL、发布时间、抓取时间、可信层级、影响方向、去重 key。
   - LLM 报告读取事件时间线，而不是直接读取未整理的搜索结果。

3. 来源分层和引用
   - 报告中的“最新动态”“风险警报”“业绩预期”必须附来源。
   - 高可信来源优先：交易所公告、监管公告、公司公告、财报、主流财经媒体。
   - 社交舆情作为情绪参考，不能单独支撑事实结论。

4. 新闻去重与过期过滤升级
   - 在现有 `NEWS_MAX_AGE_DAYS` 基础上，增加同事件聚类和重复标题过滤。
   - 避免同一新闻多站转载导致 LLM 误判为“市场高度关注”。

5. 报告结论结构化
   - 每个核心结论拆成：事实依据、推断逻辑、操作条件、失效条件。
   - 让报告更接近专业投研 memo，而不是单段自然语言判断。

### P1：适合进入产品路线

1. 自选工作台增强
   - 在首页自选列表中增加“最新事件数、最高可信事件、数据质量、报告变化、告警状态”。
   - 点击股票时，同屏看到行情、指标、事件、历史观点和本次报告。

2. A 股公告和财报结构化
   - 上交所、深交所、巨潮资讯公告可作为高可信来源。
   - 重点提取业绩预告、定期报告、分红、回购、减持、问询函、诉讼仲裁、重大合同。

3. 行业/板块上下文
   - A 股补所属申万/中证/概念板块，展示板块涨跌、板块资金、龙头对比。
   - 美股补 GICS 行业、核心同业、ETF/指数背景。

4. 导出与复盘数据包
   - 支持导出本次报告、关键指标表、事件时间线、行情快照和回测结果。
   - 先做 CSV/Markdown，后续再考虑 Excel 模板。

5. 告警系统
   - 借鉴终端的监控思路，提供价格突破、跌破止损、重大公告、数据源异常、报告观点变化等告警。

### P2：长期方向，谨慎推进

1. 宏观/行业数据库
   - 以公开数据源和用户自配 API 为基础，逐步做宏观指标、行业景气、商品价格、利率汇率等上下文。
   - 避免一开始做全量数据库，先服务股票报告质量。

2. 投资组合风险
   - 从单股分析扩展到组合暴露：行业集中度、相关性、波动、最大回撤、事件共振。
   - 适合和现有持仓管理结合。

3. 本地研究资料库
   - 保存用户上传的研究笔记、公开 PDF、公告、会议纪要摘要。
   - LLM 回答时引用用户授权资料，形成个人投研知识库。

4. 多资产扩展
   - 债券、基金、商品、外汇都可以扩，但应在股票分析主链路稳定后推进。
   - 优先顺序建议：ETF/基金 > 指数/宏观 > 债券 > 商品/外汇。

## 建议产品路线图

### 第一阶段：可信报告

目标：让用户知道报告依据是什么，哪些字段可靠，哪些地方需要谨慎。

交付重点：

- 数据来源记录。
- 来源可信度分层。
- 新闻去重。
- 事件时间线。
- 报告引用来源。
- 数据质量摘要。

这一阶段最像 Reuters + Thomson Reuters 的启发：先把事实、来源、时效和引用做好。

### 第二阶段：投研工作台

目标：让用户不需要在报告、行情、新闻、历史记录之间来回跳。

交付重点：

- 自选监控增强。
- 个股详情同屏展示行情、指标、事件、报告、历史观点。
- 告警状态和报告变化提示。
- CSV/Markdown 导出。

这一阶段最像 Bloomberg / Wind 的启发：把常用动作压缩到一个工作流里。

### 第三阶段：A 股深度上下文

目标：让 A 股分析从“个股 + 新闻 + 技术面”升级到“个股 + 板块 + 公告 + 财报 + 资金 + 政策/产业”。

交付重点：

- 公告结构化。
- 财报/业绩预告事件化。
- 板块/概念/指数上下文。
- 资金流和龙虎榜解释。
- 风险事件库。

这一阶段最像 Wind 的启发：建立中国市场本土语境。

### 第四阶段：个人研究系统

目标：把项目从“自动生成报告”升级为“持续积累和复盘的个人投研系统”。

交付重点：

- 历史报告依据快照。
- 用户研究笔记和外部资料库。
- 观点变化追踪。
- 组合层面风险。
- 回测与实际收益归因。

## 借鉴优先级矩阵

| 能力 | 来自谁的启发 | 对当前项目价值 | 实现难度 | 推荐优先级 |
| --- | --- | --- | --- | --- |
| 数据来源血缘和质量评分 | Bloomberg / Wind | 很高 | 中 | P0 |
| 新闻来源分层和去重 | Reuters / LSEG | 很高 | 中 | P0 |
| 结构化事件时间线 | Bloomberg / LSEG / Wind | 很高 | 中 | P0 |
| 报告引用来源 | Reuters / Thomson Reuters | 很高 | 低到中 | P0 |
| 财报/公告固定摘要模板 | LSEG Super Summaries / Wind | 高 | 中 | P1 |
| 自选工作台同屏决策 | Bloomberg / Wind | 高 | 中 | P1 |
| CSV/Excel 导出 | Bloomberg / LSEG / Wind | 高 | 低 | P1 |
| 告警系统 | Bloomberg / Wind | 高 | 中 | P1 |
| 宏观行业数据库 | Wind / LSEG | 中到高 | 高 | P2 |
| 组合风险分析 | Bloomberg / Wind | 中 | 中到高 | P2 |
| 交易执行 | Bloomberg | 低，且风险高 | 高 | 不建议 |
| 付费新闻/研报聚合 | Reuters / Wind | 高但授权风险高 | 高 | 不建议 |

## 对当前项目定位的建议

不要定位为“Bloomberg/Wind 替代品”。更准确、更可信的定位是：

> 开源、可自托管、可接多源数据、可追溯引用的 AI 股票投研工作台。

这个定位的优势是：

- 避免和机构级终端正面拼数据授权和实时性。
- 强调开源项目可以胜出的地方：可定制、可自托管、可接入用户自己的 Key、可解释、可复盘。
- 把 LLM 的价值放在“整理、摘要、结构化、解释、提醒”，而不是假装拥有专有金融数据。

## 参考资料

### 官方来源

- Bloomberg Professional Services: [Real-Time Market Data Feed (B-PIPE)](https://professional.bloomberg.com/products/data/enterprise-catalog/real-time-data-feed/)
- Bloomberg Professional Services: [Data License](https://professional.bloomberg.com/products/data/data-management/data-license/)
- Bloomberg Professional Services: [Real-Time Events Data announcement](https://www.bloomberg.com/professional/insights/press-announcement/bloomberg-elevates-front-office-efficiency-with-real-time-events-data/)
- LSEG: [Refinitiv has now changed to LSEG](https://www.lseg.com/en/data-analytics/refinitiv)
- LSEG: [LSEG Workspace](https://www.lseg.com/en/data-analytics/products/workspace)
- LSEG: [Reuters Super Summaries launches on LSEG Workspace](https://www.lseg.com/en/data-analytics/products/workspace/updates/reuters-super-summaries-launches-on-lseg-workspace)
- LSEG: [LSEG partners with Reuters to launch AI-driven news format](https://www.lseg.com/en/media-centre/press-releases/2025/lseg-partners-with-reuters-to-launch-ai-driven-news-for-reliable-earnings-intelligence-on-thousands-of-companies)
- Reuters Agency: [Reuters leading the future of news](https://reutersagency.com/)
- Reuters Agency: [About Reuters](https://reutersagency.com/about/)
- Reuters Agency: [Journalistic standards and values](https://reutersagency.com/about/standards-values/)
- Thomson Reuters: [Trust Principles](https://www.thomsonreuters.com/en/about-us/trust-principles)
- Thomson Reuters: [Closing of sale of Refinitiv to LSEG](https://www.thomsonreuters.com/en/press-releases/2021/january/thomson-reuters-announces-closing-of-sale-of-refinitiv-to-london-stock-exchange-group)
- Thomson Reuters: [2024 annual report announcement](https://www.thomsonreuters.com/en/press-releases/2025/march/thomson-reuters-files-2024-annual-report)
- Wind: [Wind 官网首页](https://www.wind.com.cn/portal/zh/Home/)
- Wind: [Wind 金融终端](https://www.wind.com.cn/portal/zh/WFT/index.html)
- Wind: [Wind 数据库传输服务](https://www.wind.com.cn/portal/zh/WDS/database.html)
- Wind: [Wind 指数](https://www.wind.com.cn/portal/zh/WindIndex/index.html)

### 价格与采购公开资料

- Quartz: [This is how much a Bloomberg terminal costs](https://qz.com/84961/this-is-how-much-a-bloomberg-terminal-costs)
- Cost Brief: [Bloomberg Terminal costs and pricing overview](https://costbrief.com/bloomberg-terminal-costs-pricing-overview/)
- Costbench: [Refinitiv Eikon pricing 2026](https://costbench.com/software/financial-data-terminals/refinitiv-eikon/)
- MarketXLS: [MarketXLS vs Refinitiv Eikon](https://marketxls.com/marketxls-vs-refinitiv)
- 深圳市地方金融监督管理局: [Wind 资讯金融终端服务项目单一来源采购公告](https://jr.sz.gov.cn/sjrb/xxgk/zjxx/zfcg/content/post_8343605.html)
- 四川银行: [2024 至 2025 年万得 Wind 金融终端采购项目单一来源采购公示](https://www.scbank.cn/cn/col322/1816162.html)
- 天津农商银行: [2025 年度 Wind 资讯金融终端采购项目单一来源采购公示](https://www.trcbank.com.cn/News/202412/2024120909391821.htm)
