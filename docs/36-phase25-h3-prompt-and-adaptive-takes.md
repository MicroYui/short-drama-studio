# Phase 25：H3 Prompt IR、顺序候选与相对选片

## 结论

Phase 24 的 P0 建议已经落成可复用能力，并且没有调用真实 MiniMax-H3、GPT Image 2 或其他付费媒体生成：

1. H3 提示词不再由通用自由文本承担，而是由模型专属 `H3PromptCompilerSkill` 把现有 ShotPlan、锁定对白、ReferencePlan 和媒体职责编译成 MiniMax 官方 Prompt IR；
2. `AUTO_DRAFT` 仍可设置候选上限，但默认按顺序只提交第一个候选，完成 QA 后再决定停止、追加同路线候选或转给 Director Agent；
3. 新增独立 `CompareTakesTool`，同时查看同镜候选、镜内 contact sheet、前镜边界和批准资产，返回推荐 Take 或 `NONE_ACCEPTABLE`；
4. 比较结论是建议，不是锁。用户可以只生成报告、不应用选择，也可以在自动选择后手动改选，后续 Harness 不会把用户选择强行改回；
5. 没有增加门场景专用字段、固定创作门禁、hash 冻结或第二运行时。

真实质量收益仍需下一轮 H3 样片验证；本阶段只证明结构、恢复语义、Provider 请求边界和 Agent Tool 组合已经可执行。

## H3PromptCompilerSkill

模型专属 Skill 位于 `backend/short_drama/providers/h3_prompt_skill.py`。通用项目模型仍拥有剧情事实，Skill 只负责最后一层 H3 表达，不反向污染 ShotPlan。

| 路线 | Prompt IR | 额外语义 |
| --- | --- | --- |
| `DIRECT` | `integrated_multimodal_description` → `overall_soundscape` → `non_diegetic_music` | T2VA 完整建立画面与动作 |
| `FRAME_FIRST` | 官方 0.00 秒首帧对齐行 + 三字段 | 首帧身份、服装、场景、光线和构图是开场事实 |
| `FRAME_FIRST_LAST` | 官方首尾时间对齐行 + 三字段 | 描述可观察的连续中间路径，并收敛到尾帧终态 |
| `REFERENCE` | `subject_definitions` → `summary` → `retention_analysis` → `detailed_description` → `overall_soundscape` → `non_diegetic_music` | 为每个 `<Picture N>`、`<Video N>`、`<Audio N>` 声明职责和不应复制的内容 |

通用编译器还保留以下项目约束：

- 9:16、4–15 秒整数请求和 7000 字符上限；
- 原语言对白使用 `<d>[Chinese] ...</d>`，同一提示词内说话人使用稳定 `(S1)`、`(S2)`；
- 画外音使用 `says in an off-screen voiceover`，并明确画面人物闭嘴；
- 只投影当前镜头可见资产，画外连续性道具不会因为 Bible 中存在就被重新带入；
- 动作节拍、接触点、刚性附件和不变锚点继续来自既有 ReferencePlan，而不是为单个样片新增字段；
- Motion Guide 只转移声明的运镜、节奏、尾部运动状态或屏幕方向，不复制低保真画面、旧对白、旧剪辑和偶发错误。

GPT-5.6-sol 仍可作为可选编译增强器，但它必须输出相同字段顺序；确定性校验会检查结构、时长、画幅、锁定说话人、逐字对白和媒体标签。用户也可直接编辑最终 prompt。

## 自适应顺序候选

`candidate_count` 现在表示本轮最多允许的候选数，不再表示 AUTO_DRAFT 必须同时提交的数量。

默认真实自动链路为：

```text
提交候选 1
  → 技术检查 + InspectVideoTool
  → 通过：停止该镜生成
  → 未通过且更像随机画质/构图波动：在上限内提交候选 2
  → 未通过且是空间、接触、朝向、身份、道具、对白来源或媒体技术问题：
       不盲抽相同输入，交给 Director Agent 比较换关键帧、换路线、拆镜或换覆盖
```

这套分类是 Harness 的默认建议策略：

- 手工执行制片路线时 `adaptive_sequential=false`，仍可一次提交用户明确要求的所有候选；
- AUTO_DRAFT 使用 `adaptive_sequential=true`；
- 首个通过候选会立即停止，不为凑数继续生成；
- 失败后追加候选、Director 修复和 Provider task ID 都写回现有 H3 RoutePlan 与 AutonomousRun，可在应用重启后继续；
- 没有新增预算门。候选上限只是用户或上层 Agent 的计算量选择。

离线五分钟 20 镜回归在全部首个候选通过时，从旧策略的 40 次 Fake H3 请求降为 20 次；这只证明编排不会无条件重复提交，不代表真实 H3 可用率已经提高。

## CompareTakesTool

新增接口：

`POST /api/short-drama/projects/{project_id}/agent-tools/compare-takes`

输入是一个镜头和 2–5 个当前 Take；如不指定 Take ID，则使用该镜当前可用候选。候选必须先完成单 Take QA。Tool 在一次多模态调用中读取：

- 每个候选的时间序列 contact sheet；
- 可用时的上一镜边界 contact sheet；
- 当前镜头的批准角色、场景和道具参考；
- ShotPlan、单 Take QA、语义观察和用户补充的 review focus。

比较维度保持通用：人物身份与服装、场景和道具连续性、动作与屏幕方向、对白或声音来源语义、构图与成片质感。

输出持久化为 `TakeComparisonReport`：

- `SELECT_TAKE`：只能选择已经通过单 Take QA 的候选；
- `NONE_ACCEPTABLE`：全部候选都有不应进入当前剪辑的明显矛盾；
- `UNAVAILABLE`：证据或模型无法完成可信比较时保留无结论状态。

`apply_selection=false` 只生成建议；`apply_selection=true` 才更新当前入选 Take。`NONE_ACCEPTABLE` 在自动应用时会清除该镜自动入选，随后由现有 Director 闭环处理。相同候选集合的已完成报告会复用，不重复调用模型；用户之后手动改选时，以用户选择为准。

## 持久化与恢复

- SQLite 新增普通 `take_comparisons` 表和 migration 11；
- `ProjectAggregate.take_comparisons` 让桌面端、Agent 和重启后的运行器都能读取报告；
- AutonomousRun 沿用原来的 `GENERATE → QA_AND_REPAIR → PLAN_ROUTES/TIMELINE` 状态，不增加第二套工作流；
- H3 task、provider run、RouteEvidence、inspection 和 comparison 仍使用真实项目/镜头/Take ID 关联；
- 单 Take 独立分数继续用于 QA 和无比较报告时的旧路径，但存在多个 QA-passed H3 候选时，真实自动链路优先相对比较。

## 声音边界

按已确认范围，本阶段不恢复自动声音 QA。InspectVideoTool 只检查音频流是否存在，并可报告画面可见的声音来源语义矛盾；声纹、信号质量、字幕时间和跨镜声线不会在 AUTO_DRAFT 中自动形成额外门禁。H3 的 reference audio 输入能力保持可用。

## 验证范围

离线验证覆盖：

- 四种 H3 路线的 Prompt IR 字段、首尾帧对齐、媒体标签、锁定对白和画外音；
- GPT-5.6-sol 可选编译器对错误字段顺序和改写对白的拒绝；
- 手工批量候选与 AUTO_DRAFT 顺序候选的不同执行语义；
- 构图类失败可以在上限内追加候选，空间/接触/身份等结构性失败不会盲抽；
- `SELECT_TAKE`、`NONE_ACCEPTABLE`、只报告不应用、用户改选、报告复用和重启持久化；
- 多分钟 AUTO_DRAFT、局部 Director 修复、慢任务轮询、TimelineManifest 与导出回归；
- OpenAPI 生成、Python strict typecheck 和 TypeScript strict typecheck。

所有测试使用 Fake Provider 或本地合成媒体，没有产生 H3 或 GPT Image 费用。

最终验证结果：

- Phase 25 核心回归：71 项通过；
- 后端全量回归：768 项通过；
- Pyright strict：0 error / 0 warning；
- TypeScript strict：通过；
- OpenAPI schema 与 TypeScript 类型已重新生成；
- 前端、Electron main 和 preload 生产构建：通过。

## 下一步与停点

进入任何真实 H3 提交前先停下，由用户验收本阶段实现和下一轮样片设计。验收后建议进入 Phase 26：

1. 在项目隔离和结构化/BM25 召回后增加小候选集视觉重排；
2. 新增 `KeyframeCompareTool`，比较人物、空间、动作起点、构图和错误文字，并允许 `NONE_ACCEPTABLE`；
3. 只对少量代表镜头比较旧自由提示与官方 H3 Prompt IR、单候选停止策略和相对选片；
4. 根据真实结果直接调整 Skill、Prompt 和 Harness，不建立额外实验审批层。

仍为 `UNKNOWN`：官方 Prompt IR 在当前 AceData H3 接口上的实际净增益、pairwise 结论与人工选片的一致率、结构性失败分类的跨剧本稳定性，以及 Motion Guide 对不同镜头类型的真实收益。
