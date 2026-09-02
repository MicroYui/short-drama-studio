# Phase 13：通用视频与抖音短剧制作策略

> 后续状态：Phase 14 已将此处的 45–60 秒规划基线扩展为用户可自定义的目标时长；Phase 23 将这些 Prompt Policy 收敛为显式 `ProductionSkill` 与静态 Catalog；Phase 28 进一步把 Skill 拆为稳定 constitution、角色 rubric 和上下文经验，并让资产、H3 路由/Prompt 编译及整集 Review 按职责读取 Skill。本文保留 Phase 13 当时的实现记录。

## 目标

同一套本地制片系统支持两种创作取向，用户在新建项目时选择：

- `GENERAL_VIDEO`：节奏服从内容，允许氛围、留白、观察和有意义的长镜头；
- `AI_SHORT_DRAMA`：强调前置冲突或好奇缺口、因果悬念链、关系/认知变化和结尾回收。

这不是两套产品，也不是把 Profile 直接塞给 H3。两种类型继续共享资产库、ReferencePlan、Director、H3 路线、Take、QA、时间线和导出；差异只进入 Writer、Production Planner 和 Director 的 Agent 判断。

## 实现

项目请求和持久化 `ProjectRecord` 新增 `production_profile`。旧项目缺少该字段时按本产品既有语义读取为 `AI_SHORT_DRAMA`，独立重复会继承源项目 Profile。

`production_profiles.py` 集中维护两套 Prompt Policy：

| Agent 责任 | 通用视频 | 抖音短剧 |
| --- | --- | --- |
| Writer | 允许氛围、沉默、观察和非反转叙事，不为模仿短剧强造冲突 | 尽早建立好奇缺口或张力，以因果变化推进并埋设、回收转折 |
| Production Planner | 用情节、表演、情绪、节奏或空间意义解释时长，不为了密度增加剪辑 | 判断每个镜头为什么值得占据当前时长，压缩空转，但不机械堆镜头或对白 |
| Director | 不因安静或长镜头本身判错，只看是否完成声明的意图 | 区分有效悬念/表演与空等/空走，根据实际证据推荐重生成、换关键帧、拆镜或换机位 |

当前 Writer 与 Production Planner 仍在同一次结构化规划调用中协作，避免为了角色名额复制一套运行时；Director 在候选 QA 后读取同一项目 Profile。

Phase 13 当时的本地回退曾分别使用“都市悬疑”和“4 镜、50 秒生活观察”模板，用于验证 Mock 候选、时间线和导出链路。后续已移除这些默认创作选择：当前回退只产生满足 Schema 的最小结构骨架，未指定的 Style 保持未指定，场景、镜头、时长和节奏由 Agent 结合当前项目决定。

## 分镜边界

本阶段移除了原先“必须 6–10 镜”的规划 Schema 限制，也允许无对白计划。Planner 只受当前交付目标和 Provider 技术边界约束：全片目标 45–60 秒，H3 单段 4–15 秒。镜头数量由素材自然推出，不新增以下门控：

- 每隔固定秒数必须出现新信息；
- 每镜至少若干动作或信息拍；
- 长镜头自动拒绝；
- 抖音模式必须达到固定镜头数或对白数。

Director 可以建议某个镜头没有赚到它的时长；`GUIDED` 用户可忽略，`AUTO_DRAFT` 也只是由 Agent 选择改 Prompt、换关键帧、拆镜或换机位，不形成底层专用字段。

## 前端

新建项目页用两个说明卡选择制作类型，默认抖音短剧。项目列表和总览显示当前 Profile。输入仍可为空、图片、一句话或锁定剧本，运行仍可选择 `AUTO_DRAFT` 或 `GUIDED`。

## 验证边界

本阶段验证的是 Profile 在 API、持久化、独立重复、Planner、Director 和前端请求中的工程闭环，并用本地回退/Fake Provider 验证不同策略文本和可忽略建议。没有调用 GPT Image 2 或 H3，因此不能把本阶段写成两种 Profile 的真实成片质量对比。

工程结果：

- 后端全量测试：687 passed；
- Pyright：0 errors；
- OpenAPI 与 TypeScript 类型已重新生成；
- TypeScript `tsc --noEmit`：通过；
- Vite 前端、Electron main 与 preload 生产构建：通过。

后续如要评估策略质量，应对同一输入分别创建两种 Profile 的独立项目，比较信息密度、故事清晰度、长镜头有效性和最终观看体验；不应为通过指标反向规定固定镜头数。

## 范围边界

- 不复制第二套 Agent Runtime 或工作流；
- 不向 H3 请求增加 `production_profile` 专属字段；
- 不新增“爆点数量”“信息拍数量”等领域字段；
- 不把 Agent 建议变成审批门；
- 不为实现验证调用付费 Provider。
