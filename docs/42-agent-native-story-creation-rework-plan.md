# Agent-native 剧本创作链路改造与验证计划

状态：执行中

适用范围：`backend/short_drama` 的剧本规划、逐场写作、审校、修订、经验沉淀与相关文档

当前验证边界：先完成剧本，不进入完整分镜、资产、图片、视频或桌面编辑器

## 1. 最终目标

把当前偏“统一大 Prompt + 单一格式 Skill + 多轮严格审校”的剧本链路，改造成真正 Agent-native 的创作系统：共享角色只承担跨题材成立的通用职责；宽粒度格式 Skill 表达 AI 短剧、数字人、动漫等媒介差异；Agent 根据用户需求的语义自主选择零个或多个可组合 Narrative Skills；当前故事的观看目标与创作方向通过版本化的 `StoryIntent` 贯穿规划、写作和修订；Critic 以目标观众体验和创作忠实度为中心，能接受符合调性的夸张、偶然、程序压缩和无厘头表达。

系统最终需要独立完成三分钟中文竖屏 AI 爽剧“所有人的数学水平倒退 100 倍，只有我不变”的完整剧本闭环，并留下用户可掌控的语义化 trace。出现代码或运行时 bug 时修复后从头重跑；如果只是成稿效果不佳，保留本轮输出和 trace，定位所属能力层后再做针对性改进。

## 2. 不可退化的设计原则

1. 不使用关键词正则、题材白名单、硬编码映射或隐藏分类器决定创作方向。
2. 默认只做一次完整 Critic，后续聚焦修订由增强 Verifier 验收和自然冷读；不设置固定爽点数、逻辑分数阈值、聚焦修订次数或质量门禁。
3. 每次 `planning.advance` 推进一个可持久化工作单元，只用于恢复、进度可见和上下文控制，不代表固定创作步骤或次数限制。
4. Skill 提供思考维度和创作方法，不限制 Tool、输出形式、审批流程或 Agent 的自主判断。
5. `ProjectDocument` 始终是内容真相；`PlanningRun` 是可恢复的领域子任务游标；`AgentRun` 是总调度与 trace，不建立第二套内容真相。
6. `StorySourceContract` 只保存用户明确承诺、禁止项和开放选择；Agent 对本故事的解释不得冒充用户合同。
7. 所有长篇创作指令使用中文 Markdown；JSON 字段、枚举、Tool ID 和 Provider 协议字段可保留英文。
8. 按普通产品工程推进，不额外引入安全审计、密码学证据链、冻结层级或审批门。
9. 暂不考虑多实例并发、预算上限、桌面端编辑器和 LTX 自带功能扩展。
10. 不调用公共 npm registry，不安装或引导下载依赖；JavaScript 检查只使用仓库已有本地二进制。
11. 正则可以用于 Markdown/JSON 协议边界、SQLite 全文检索、资产搜索和 ffmpeg 元数据等技术解析；不得用它或直接关键词匹配替 Agent 做题材、Skill、修订、候选或生成路线判断。

## 3. 概念与数据边界

### 3.1 `StorySourceContract`

保存用户明确提出的内容要求、禁止项和真正留给 Agent 的选择空间。修复当前“把整段原始输入仅作为一条 commitment”的初始路径，改为由 Agent 语义提炼，同时保留原始需求的可追溯性。

### 3.2 `StoryIntent`

不采用 `StoryExperienceContract`。`Contract` 留给用户明确承诺，`Experience` 留给有运行证据的 `ExperienceMemory`。新增轻量、版本化、自然语言表达的 `StoryIntent`：

- `audience_experience`：希望目标观众主要获得什么体验，以及继续观看的动力。
- `dramatic_engine`：核心设定怎样持续产生行动、冲突、反应和回报。
- `progression_strategy`：体验怎样推进，可以是升级、积累、反差、慢燃或循环变奏。
- `tone_and_plausibility`：现实可信度、夸张、荒诞、巧合和情绪真实之间的关系。
- `protected_elements`：修订时不能无意磨掉的反差、趣味、人物魅力或名场面效果。
- `drift_risks`：本轮创作最容易滑向的错误方向。

这些字段不使用题材枚举、数值评分、满足状态或通过布尔值。`StoryIntent` 先进入 `EpisodeOutline`，完成规划后随故事版本写入 `ScriptVersion`；不得只存于 `PlanningRun`。`StorySourceContract` 也应随最终故事版本持久化，保证长期追溯不依赖运行游标。

### 3.3 其他对象

- `ProductionProfile`：只表示 AI 短剧、数字人、动漫、通用视频等宽粒度生产格式，不增加“AI 爽剧”等题材枚举。
- `ProjectBible.style`：只表达画面、声音、摄影、色彩、材质、媒介和视觉禁区，不承载完整叙事目标。
- `EndingContract`：只负责本集兑现、结尾落点和是否保留续集动力。用户明确不要续集时给出完整闭环；用户未限定时由 Agent 根据作品目标判断。
- `ExperienceMemory`：只沉淀有真实运行证据的经验，并记录适用条件与不确定性，不把单次失败升级成全局规则。
- 人物、地点、道具和台词归属使用稳定的 `character_id`、`location_id`、`prop_id` 与 `speaker_character_id` 贯穿蓝图、修订和文档投影；名称只用于展示，不作为身份匹配键。参考绑定使用结构化 `kind`、`target_id`、`purpose` 与 `required` 布尔值，不从自由文本推断是否必需。

## 4. Prompt 与 Skill 分层

最终 Prompt 装配顺序：

```text
中文共享角色能力
+ ProductionProfile 对应的格式 Skill
+ Agent 本轮语义选择的 Narrative Skills
+ 当前 StoryIntent
+ 与任务相关的 ExperienceMemory
+ 本轮内容数据、上下文与输出 Schema
```

### 4.1 中文共享角色能力

把 Story Architect、Writer、Critic、Repair Planner、增强 Revision Verifier 等共享、可复用的长篇创作职责迁入 `skills/shared/roles/` 下的中文 Markdown。Python 负责装配运行时事实、当前数据、结构协议与紧贴本次输出 Schema 的局部执行说明，不在代码中复制另一套通用创作政策。旧 `FINAL_COLD_READ` 类型只作历史运行与持久化兼容，不是现行角色阶段。

通用 Story Architect 只负责：

- 区分用户承诺、禁止项和开放创作空间。
- 建立人物处境、欲望、阻碍、行动、选择、回应和后果。
- 管理观众已经知道、能够推断和仍在追问的信息。
- 让每场戏改变某种故事状态，并形成可供 Writer 扩写的蓝图。
- 按当前调性维持最低充分因果、时长和连续性。
- 兑现本集观看承诺。
- 不处理镜头、资产、图片、视频或 Provider 语法。

从共享 Story Architect 中移除“技术、制度、程序能力必须提前建立”“默认 grounded explanation”“现实逻辑完整度天然高于爽感、喜剧或荒诞”等偏好。

### 4.2 格式 Skill

保留现有 `ProductionProfile` 与格式 Skill。`AI_SHORT_DRAMA` 只沉淀相对稳定的媒介差异：低理解成本、尽快读懂观看承诺、通过行动/反应/状态变化推进、情绪回报可见、适配手机观看和生成式生产。它不再默认规定所有短剧必须打脸、严格悬疑铺垫或现实程序完整。

### 4.3 Narrative Skills

新增开放、文件化、可组合的 `skills/narrative/`。Loader 允许 Skill 只支持相关角色，不再要求每个 Skill 实现全部角色。代码只校验 Agent 返回的 Skill ID 是否存在、是否可加载、是否支持当前角色，不判断其题材“是否正确”。

首批只实现已有需求和研究支持的三个能力：

- `premise_amplification`：让核心高概念持续工作，在不同局面中产生新应用，而不是开头出现后被普通剧情替代。
- `payoff_escalation`：可见反应、见证范围扩大、权威或社会层级进入、主角地位/资源/选择权变化，以及更大舞台上的新回报。
- `absurd_comedy`：保持核心规则和行动—结果关系清楚，同时允许夸张群体反应、权威快速到场、现实程序压缩、巧合和无厘头升级。

“食堂算菜价—勺子掉落—同学震惊—校长赶来”只作为方法说明和回归样本，不得成为固定节拍模板。现实悬疑、克制情感或数字人口播不应自动加载这些 Skill。

### 4.4 Agent 语义选择

在正式蓝图前增加一个可恢复的创作定向单元。Agent 阅读原始需求、格式 Skill 和紧凑的 Narrative Skill 目录，语义输出：

- `StorySourceContract`
- 所选 Narrative Skill IDs
- 每项选择或不选择的理由
- `StoryIntent`

选择结果写入现有 `active_skill_ids`、DecisionMemory 和 trace。下一次 `planning.advance` 才加载完整 Skill 正文并生成蓝图。用户改变题材或调性时，Agent 可重新判断；没有明显匹配 Skill 时允许只使用格式 Skill 和通用能力。

## 5. 写作、审校与修订链

### 5.1 Writer

- 读取 `StoryIntent`、本场目标、相邻场状态和相关 Skill，不携带整条历史。
- 实现 `playable_actions` 的戏剧意图，允许合并、压缩、重排和视觉替代，不要求规划文本逐字进入剧本。
- 只有用户明确锁定的对白或文字需要逐字保留。
- 如果蓝图已经违背 `StoryIntent`，可请求上游重规划，不强行扩写错误方向。
- 按逐场持久化方式持续刷新进展，禁止退回“一次调用直接生成整部剧本”。

### 5.2 Critic

保留 Critic，但把它定位为有记忆的目标观众冷读者，而不是逻辑审计器。它同时检查最低成立性和创作忠实度：核心设定是否仍在工作、用户要求的类型是否可识别、目标体验是否被兑现、主角是否获得可见变化、修改会不会“更正确但更无聊”、当前调性是否允许现实程序压缩。

持久化轻量 Issue Ledger，包含语义问题簇、状态、文本证据、修复证据、回归或深化、已接受取舍和交接方向。问题 ID 与聚类由 Agent 语义维持，不用正则生成。每条观察由 Agent 决定进入剧本修订、下游导演、可选建议或接受的调性压缩；严重度和数量不自动触发修改。

### 5.3 Repair / Verifier / Manager

- Repair Planner 按语义问题簇做连贯修复，区分用户承诺和 Agent 自己的创作选择，不把临时细节固化成新合同。
- 默认审校链为一次完整 Critic → Repair Planner/逐场修订 → 增强 Revision Verifier。Verifier 先验证本轮修复范围、`StorySourceContract`/`StoryIntent` 是否保留以及是否引入回归，再脱离修复说明对当前完整剧本做自然冷读，不重新做一次全局 Critic；所有结论与理由完整进入 trace。
- 增强 Verifier 直接接受当前稿，或带具体 issue、场次范围和理由继续聚焦修订；聚焦修订后再回到 Verifier，不重跑完整 Critic，也不进入独立 Final Cold Read。
- Script-only 链路不保留空转的 ReviewRouter；只有真实跨媒体责任分派出现时才使用单独 Router。
- 增强 Verifier 的全稿自然冷读重点判断目标观众是否会把它认作用户要求的作品、核心设定在中后段是否仍活跃、结尾是情绪回报还是事务清单。

## 6. PDF 与视频生成经验的沉淀边界

MiniMax H3 PDF 中可复用的内容继续沉淀到端到端流程、导演、资产和 Provider 层：

- 核心角色和场景资产结构化、统一命名与可追踪引用。
- Agent 自主判断角色是否需要标准四视图，群像或次要角色不强制重资产化。
- 多镜头场景可建立俯视/全景、具体区域全景等层级资产。
- 剧本完成后再进行时间线拆分和逐段定镜头。
- 每段明确镜头、动作、台词、情绪、声音与画面同步。
- 最终 Provider Prompt 通过真实 `@资产` 绑定引用媒体。

这些要求不进入通用 Story Architect，也不属于本轮“完整剧本”验收范围，但相关文档与职责边界必须同步一致。

## 7. 实施阶段

### 阶段 A：基线与迁移保护

1. 记录当前真实调用链、Prompt 装配点、模型结构、PlanningRun 状态和测试覆盖。
2. 识别当前工作树中已有改动，所有实施仅增量修改相关文件，不覆盖用户工作。
3. 固化数学退化剧现有失败输出为质量回归材料，明确它是项目经验而非通用规则。

### 阶段 B：中文共享角色与边界重构

1. 建立共享角色 Markdown 加载机制。
2. 中文化剧本链全部创作 Prompt。
3. 清除 Story Architect 的默认现实主义和程序化偏置。
4. 更新 AI 短剧格式 Skill，只保留格式层稳定差异。

### 阶段 C：可组合 Skill 与语义发现

1. 扩展 Skill 描述和 Loader，使单个 Skill 可只实现部分角色。
2. 扩展 Prompt 编译器支持格式 Skill 与多个 Narrative Skills 独立组合。
3. 增加创作定向单元，持久化选择理由、`active_skill_ids`、DecisionMemory 和 trace。
4. 实现首批三个 Narrative Skills。

### 阶段 D：`StoryIntent` 全链路

1. 增加模型、序列化、仓储与向后兼容。
2. 贯通 `EpisodeOutline`、`ScriptVersion`、ProjectDocument 检索和版本修订。
3. 将 `StorySourceContract` 一并持久化到最终故事版本。
4. 将两者按最小必要上下文传入 Writer、Critic、Repair 和增强 Verifier；全稿自然冷读由 Verifier 内部完成。

### 阶段 E：Writer 与审校链重构

1. 删除普通模式下 `playable_actions` 的逐字覆盖校验。
2. 建立 Issue Ledger 与语义状态延续。
3. 调整 Critic、Repair、Verifier、Manager 的回流和上下文裁剪。
4. 移除或合并 script-only 空转 ReviewRouter。
5. 保持无固定轮次、无死超时的 Agent 自主推进；长任务按进度持续刷新，不以 300 秒死值判断创作失败。

### 阶段 F：文档沉淀

同步更新：

- `backend/short_drama/skills/README.md`
- `backend/short_drama/skills/workflows/end_to_end.md`
- AI 短剧格式 Skill 与相关角色说明
- 架构迁移、决策登记和研究文档
- PDF/H3 经验在导演、资产、Provider 层的正确落点

清理与新边界冲突的旧说明，明确“已确认决定”和“仍需实验结论”。

### 阶段 G：结构、集成与效果验证

结构和集成测试至少覆盖：

- 共享角色、格式 Skill、多个 Narrative Skills 可独立组合。
- 所有剧本创作 Prompt 使用中文。
- Agent 选择的 Skill 实际传播到 `active_skill_ids`、DecisionMemory、trace 和相关角色。
- `StoryIntent`/`StorySourceContract` 跨蓝图、逐场写作和修订保持一致。
- 用户改变调性后可重新选择 Skill。
- Writer 不依赖动作字符串逐字匹配。
- Critic 能接受符合 `StoryIntent` 的荒诞压缩。
- 不同题材和格式不会被错误注入爽剧 Skill。
- 场次只要求正时长，整集目标在最终场精确守恒；不得把 H3 单段 4–15 秒反向写成剧本或分镜最小时长。
- 无语义 Production Manager 或 H3 Route Agent 时，非终态工作保持等待显式 Agent 决定，不能由本地 fallback、QA 分数或关键词猜路线。

使用仓库已有 Python 环境运行相关 backend 测试和 Pyright；JavaScript 只调用现有 `node_modules/.bin`。缺少本地依赖时准确报告，不访问外部 registry。

## 8. 真实模型验收

主验收题：约三分钟中文竖屏 AI 爽剧，“所有人的数学水平倒退 100 倍，只有我不变”。本轮只生成完整剧本，在完整分镜、资产、图片、视频和时间线之前停止。Agent 必须独立运行，中途不人工干预创作选择。

Trace 至少展示：

1. 用户要求的语义提炼与开放选择。
2. Narrative Skill 的选择/不选择理由。
3. `StoryIntent`。
4. 故事蓝图。
5. 逐场写作进度和产物。
6. Critic 观察、Issue Ledger、修复和验证。
7. 增强 Verifier 的 Issue 修复验收、脱离修复说明的全稿自然冷读、接受或继续聚焦修订的语义决定，以及最终停止原因。

同时运行至少两个污染对照：现实悬疑、克制情感短剧；必要时增加数字人口播格式。它们不得自动继承连续打脸、群体震惊或荒诞升级方法。

效果比较采用同模型、同条件下的成对或盲评和具体失败分析，记录首稿耗时、完整耗时、调用分布、重复打开的问题簇、修订轨迹和最终文本，但不设置运行时或分数门槛。评估关注：题材识别、节奏、核心设定利用、场景变化、爽点/爆点/趣味、人物选择、结尾回报和整体可看性。

## 9. 完成定义

只有同时满足以下条件才可将 Goal 标为完成：

1. 上述模型、Prompt/Skill 分层、语义选择、Writer/Critic 链路和文档均已实现。
2. 相关 backend 测试与 Pyright 通过；可执行的本地前端检查也已完成，无法执行项有明确原因。
3. 主验收题从头独立跑通，输出完整 trace 和完整剧本，并在分镜前停止。
4. 对照题证明爽剧能力没有污染其他题材或格式。
5. 对真实输出做过效果复盘；若仍有明显质量问题，已经定位和修复后重新验证，而不是仅以结构测试宣告完成。
6. 最终报告列出修改文件、验证命令与结果、trace、剧本、实验结论、仍未知事项和 Git 状态。
7. 不推送远端，除非用户另行明确要求。
