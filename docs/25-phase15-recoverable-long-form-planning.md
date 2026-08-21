# Phase 15：可恢复的长篇剧本规划

## 目标

Phase 14 已证明分层 Multi-Agent 能生成多分钟剧本，但一次项目创建仍会同步执行 Story Architect、Script Critic、最多两轮修订和所有 Scene Director。真实 5 分钟样本需要多次、每次几十秒到数分钟的语言模型调用；在这些调用全部结束前，前端只能显示一个笼统等待状态，也没有可供重启恢复的项目。

Phase 15 将长篇剧本规划改为独立、持久化、逐步推进的 `PlanningRun`。本阶段只检查剧本、大纲、场次、对白和连续性，不调用 GPT Image 或 MiniMax-H3。用户仍可自由填写 15 秒到 30 分钟的任意目标时长；3/5/10/15 分钟继续只是快捷值。

## 状态模型

长篇规划在项目正式建立前写入独立 SQLite `planning_runs` 表，而不是创建缺少 ScriptVersion、ProjectBible 和 ShotPlan 的占位项目。

```text
STORY_ARCHITECT
  → SCRIPT_CRITIC
  → STORY_REVISION（需要时，最多两轮）
  → SCRIPT_CRITIC（逐轮复审）
  → SCENE_DIRECTOR 01
  → SCENE_DIRECTOR 02…N
  → FINALIZE
  → COMPLETE
```

`PlanningRunRecord` 保存：

- 原始创建请求、制作类型和自定义目标时长；
- 当前阶段、已完成阶段和错误状态；
- 整集 `EpisodeOutline`；
- 当前 `ScriptReviewReport` 与修订轮次；
- 已完成场次的 `SceneShotPlan`；
- 下一场序号、总场数和场次修复/回退标记；
- 最终 `project_id`，以及 AUTO_DRAFT 后续使用的 `autonomous_run_id`。

一次 `advance` 只执行一个 Agent 阶段或一个 Scene Director 场次。每次成功后立即更新 SQLite，语言模型调用期间不持有全局 Service 锁。相同进程内对同一 run 的重复推进用普通互斥锁拒绝；应用重启后从数据库中的当前阶段继续。语言生成本身不产生不可逆外部副作用，因此进程中断后重试当前节点即可，不需要复杂提交协议。

## API 与桌面交互

新增 Product API：

- `POST /api/short-drama/planning-runs`：立即建立恢复点；
- `GET /api/short-drama/planning-runs`：发现重启前未完成的规划；
- `GET /api/short-drama/planning-runs/{id}`：读取当前进度；
- `POST /api/short-drama/planning-runs/{id}/advance`：推进一个节点。

超过 90 秒的新项目走 `PlanningRun`；短片继续保留原有同步创建兼容。桌面端显示当前 Agent、修订轮次、审稿问题数和 `已完成场次/总场数`，不再用一个全屏等待覆盖整条长流程。若当前节点进入 `NEEDS_ATTENTION`，用户可重试；已完成大纲、审稿、修订和场次不会重跑。

AUTO_DRAFT 在 `FINALIZE` 完成后才建立原有 `AutonomousRun`。规划阶段不会创建 image task、H3 generation task 或 provider run；随后是否自动进入真实 H3 或 Mock 成片，仍由用户创建时选择的运行模式与执行目标决定。

## 单场与整集一致性

Phase 15 将两个真实探针暴露的通用问题前移到最小负责层：

1. Scene Director 的 `reference_needs.subject_name` 必须引用 Story Architect 已声明的人物、地点或道具。若单场临时发明了新资产，当前场立即进入结构修复；修复仍无效才仅该场本地回退，不再等到 `FINALIZE` 才让整集失败。
2. 锁定剧本解析器区分“说话人：台词”和常见故事契约标题，例如核心场景、关键规则、结尾明确、制作要求。说明标题不会再被误认为角色或锁定对白。

这些校验保护跨 Agent 合同，不规定题材、门、区域、爆点数量或镜头节拍，也没有增加单一样片专用领域字段。

## 建议与失败语义

- Script Critic 的建议仍是开放式、可忽略建议；最多两轮自动修订后，即使仍有问题也会进入逐场规划。
- Critic 不可用时保留结构校验并继续，不形成审批门。
- Scene Director 一场失败不会删除已完成场次；重试只使用当前场契约和上一场实际 handoff。
- 未配置 GPT-5.6-sol 时，各节点使用本地可复现回退，供 API、持久化和前端恢复验证；这不代表真实剧本质量。
- 自动流程不运行声音 QA；可选角色参考音频输入仍保留在后续制片阶段。

## 工程验证

- 自定义 430 秒目标通过分阶段 Fake Language Client 完成项目；
- Story Architect、Critic、一次修订、复审和逐场规划可在 Service 重建后继续；
- 单场瞬时失败保留同一 `next_scene_sequence`，重试后只新增当前场；
- AUTO_DRAFT 的长篇规划完成后只建立后续运行状态，尚未产生 image/H3 task；
- 未配置语言模型时，Product API 可用本地回退完成整条 PlanningRun；
- 新增回归覆盖未声明参考资产的单场修复，以及常见剧本说明标题不被解析为说话人。

### 真实 GPT-5.6-sol 剧本侧探针

使用 120 秒锁定剧本《婚礼前夜的报警录音》完成一次真实规划：

- Story Architect 产生 3 场大纲，Script Critic 触发 2 轮自动修订与逐轮复审；
- 第 1 场完成后重建 Service，恢复时保留已完成场次，从第 2 场继续；
- 最终形成 3 场、16 镜、120.0 秒规划，8 句锁定台词的文本、顺序、说话人与 VOICEOVER 来源全部保持；
- 端到端墙钟时间约 9.1 分钟，其中每个节点在 34–87 秒后单独持久。这只是当前本地端点的单次运行记录，不是稳定性能基准；
- 最终 Critic 仍保留 6 项可选建议，其中 2 项为高严重度，主要指向“未来录音为何错误指认程野”和“受贿证据与顾岚死亡威胁的因果脱节”。这说明恢复闭环已成立，但两轮修订不等于剧本质量必然通过；
- 运行结束时 `image_tasks=0`、`video_tasks=0`、`provider_runs=0`，没有调用 GPT Image 或 MiniMax-H3。

真实运行还暴露并修复了两类通用锁定剧本解析错误：“许宁问/程野回答”不再把说话动作并入角色名；“许宁对程野说”不再把对话对象误判为说话人。

阶段出口共有 710 项后端测试、Pyright、TypeScript 与 Vite/Electron 生产构建通过。本阶段不形成新的视觉、视频、声音或口型质量结论。
