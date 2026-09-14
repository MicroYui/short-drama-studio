# 校园短剧：真实 H3 执行与 Agent 运行记录

更新：2026-09-08。沿用 [H3 前准备实验](48-pre-h3-agent-case-trace.md) 的同一项目、数据库、批准资产和 12 份 RoutePlan。本阶段已完成：12 组真实视频均已回收到本地，覆盖 21 个来源镜头，实际素材总长 135.68 秒；12 个 Take 都有完成的语义观察。后续 AgentRun 于 2026-09-08 08:36:15 UTC 进入 `COMPLETED`、`goal_complete=true`。

本文保留视频生成阶段的结果。随后已完成全部选片、原生音轨字幕对齐和131秒完整导出，见 [完整成片与Agent后期记录](50-complete-film-agent-case-trace.md)；下面的“未选片”描述均指本阶段结束时的状态。

## 首组实际结果

| 项目 | 记录 |
| --- | --- |
| 项目 | `project-11b4f7a637c3`，全世界数学能力下降100倍：食堂篇 |
| AgentRun | `agent-run-11d089a4853849ba83fe4a5b88fe3028` |
| GenerationUnit | `clip-group-independent-v7-001` |
| H3RoutePlan | `h3-route-plan-18ea3092929b4f00b09e0ec1dbff8c53` |
| Task | `task-9ce8b7b428f34d39bb06c67de66da13f` |
| Provider task | `bffbe1b4-4802-460b-af8d-564f20a30798` |
| Take | `take-task-9ce8b7b428f34d39bb06c67de66da13f` |
| 请求 | MiniMax-H3，768P，9:16，13 秒，6 张参考图片，单候选 |
| 本地媒体 | 13.67 秒，768×1344，24 fps，带音轨，3,108,543 字节 |
| 生成提交 | 一次 POST `/minimax/videos`；200 返回任务 ID，未触发同步回退 |

完整 Prompt、有序媒体和实际请求中的文本均逐项匹配准备阶段保存的方案，没有重写或重新编译。真实提交的 JSON 为 11,627,798 字节；其图片编码保存在原 Provider 请求及 trace 文件中，没有重放进 Agent 文本上下文。Provider 返回 output_seconds=13、input_image_count=6；这些是使用量字段，不是已核对的账单。

视频：`output/agent-asset-validation/math-2min-asset-media-context-v17-20260907-034/_app_data/outputs/short-drama-projects/project-11b4f7a637c3/takes/clip-group-independent-v7-001/h3-task-9ce8b7b428f34d39bb06c67de66da13f.mp4`。

操作员使用本地已缓存的 `openai/whisper-small` 对同一媒体进行独立只读转写，识别出“刘阿姨这两个十四元你张口就来这不得好好算算”，与三句来源对白去标点后吻合。该记录不属于产品 Agent 的音频 Tool 调用，也不证明说话人身份、口型同步或完整声效；来源、模型目录、WAV 和原始转写均已保留。

首轮视觉观察保留了餐盘起始状态等实际偏差。原语义检查还把整段三镜头 Take 与首个 4 秒 ShotPlan 比较，产生错误的时长结论；修复后对同一视频的观察确认约 3.987 秒和 7.974 秒的切镜与来源窗口吻合，移除了这一误判。起始空餐盘被生成为已有食物、结尾计算器接触动作没有充分呈现的问题仍保留；首组运行结束时，Take 为 `REJECTED`、未选片，没有追加付费生成。首组 AgentRun 于 2026-09-08 06:48:35 UTC 完成。技术可播放、观察任务已完成和画面是否适合最终选片是不同结论。

## 后续 11 组执行

第二个 AgentRun 为 `agent-run-3e5743763adf48cc9f42cc427d63616c`，记录目录为 `output/agent-video-generation/math-2min-real-h3-20260908-002/`，目标原文保存在 `objective.md`。它复用第 002–012 组现有计划，每组仅执行原有的一个候选；根据实际任务状态回收视频并记录内容覆盖和可见偏差。本阶段完成回收、观察及覆盖核对，正式选片、时间线与最终导出属于下一阶段。

本阶段结束时，第 001–012 组各有一个真实来源 Take，原计划总时长 131 秒，实际素材总时长 135.68 秒；12 段均可完整解码，为 768×1344、24 fps 并带音轨。真实 H3 生成提交总数为 14 次：12 个原候选，以及第 011、012 组各一次参数修复重试。视频、抽帧和本地转写入口见 [MEDIA.md](../output/agent-video-generation/math-2min-real-h3-20260908-002/MEDIA.md)。

第 002 组已回收 14.38 秒视频，本地转写识别出“17还是14哎呀怎么越算越乱别急照着按8加6”，与该组两句来源对白含义吻合。修复调度后，实际记录显示第 003–006 组同时处于远端 RUNNING；其余工作由现有执行器按空闲 Worker 继续推进。计划独立性、实际任务并发和模型调用并发分别可从 PlanGraph、任务状态和模型调用时间核对。

第 011、012 组第一次提交得到远端任务 ID 后，以 `bad_request` 结束：分别有一张 344×976 的角色图（比例 0.352）和一张 1536×512 的文字板（比例 3.0），超出了返回错误中明确的 `[0.4, 2.5]` 范围。这不是内容过滤。修复后只为这两个原候选各重提一次，保留原失败任务、`retry_of_task_id` 和递增的 attempt；不能把这段记录描述为每组一次提交全部成功，也不假定失败请求已经退款。

第 008 组还出现了一次不完整交接：Worker 在 Task 仍为 QUEUED、没有 Take 时返回 COMPLETED。操作员发现后明确补齐回收职责，Leader 在 revision 4 复用该任务继续推进。这里的遗漏由操作员指出，不算作 Agent 自主发现；未增加依据任务名称或状态强制判定所有 WorkOrder 完成与否的规则，因为单独入队也可以是合法的局部职责。

## 最终覆盖和观察结果

最终 [assessment.json](../output/agent-video-generation/math-2min-real-h3-20260908-002/assessment.json) 确认 12/12 个单元、21/21 个来源镜头都有本地 Take，12/12 个 Take 都有最新 `COMPLETE` 语义观察；没有缺失观察的 Take。14 份实际请求的 Prompt、有序媒体均与已保存方案一致，发送文本也逐份匹配。旧失败任务保留，因此数据库是 12 个成功任务与 2 个历史失败任务。

历史上共保存 21 份观察报告，包含调试时对同一视频的重查。修复按 Take 保留结果后，最后 4 个 Worker 各负责 2 组，每个 Take 在各自工作单中检查一次便完成交接，没有再次交替重查；对应结果见 `fixes/010-per-take-inspection-working-set/live-followup-proof.json`。第 007 组的旧报告正文曾在交接中丢失，Leader 本轮又安排了一次观察，这个重复也计入历史报告数。

本阶段保留的主要编辑线索包括：第 001 组的起始餐盘状态、第 002 组的计算器与价牌动作、第 007–009 组的指示条款/持盘/协议文字与签字笔末态、第 010 组未清楚表现加菜回报，以及第 011–012 组通知书结构和交接末态。Agent 还把第 004 组的通道构图标为场景连续性问题；画面中仍有取餐台，该判断需要结合前后视频和批准场景参考再决定。观察是编辑依据，不能把每条模型判断都当成独立核验过的事实。

结构化媒体属性来自视频摄入与本地 FFmpeg：12 段均为 768×1344、24 fps，存在音轨。部分静帧语义摘要把分辨率、帧率或音轨标成 UNKNOWN，反映其静帧判断的限制，不能覆盖结构化探测值；对白、声线、口型、声音质量和完整音画同步仍未由产品 Agent 实际听音验收。全部 12 段都有操作员独立的本地 Whisper 转写，来源与结果已分开标注。

本阶段结束时，12 个 Take 都未选片；第 001 组保留此前 `REJECTED` 评定，其他 11 组为 `PENDING`，没有为了结束实验改写为内容全部通过。后期选片与导出也保留了这些原始QA状态。视频和逐 Take 原始观察报告可从 [MEDIA.md](../output/agent-video-generation/math-2min-real-h3-20260908-002/MEDIA.md) 直接打开。

## 真实执行暴露的问题与修复

| 问题 | 修复与记录 |
| --- | --- |
| H3ProviderRun 持有完整 data URI，可能进入下一轮模型文本 | 模型投影保留 Prompt、资产版本、路径、请求参数和任务状态；编码留在原始请求。同步覆盖 ToolAttempt 显式读取和 ArtifactSummary。此项在实际提交前核对源码并修复。 |
| Leader 读取路线列表后丢失精确引用，重复查询 | 列表返回当前路线引用及可重读的紧凑索引。当前 12 组映射索引为 1,714 字符。 |
| 同一 RUNNING 状态每次都经语言模型决定再次轮询 | 执行器按 15 秒间隔继续已确认远端 ID 的 H3 查询，并持久化每个 Tool turn；结果、错误和需恢复状态交回 Worker。进程重启也沿用同一任务。 |
| Task 成功已落盘 Take，但工具仅交回 Task 引用 | 成功结果补齐 Take 引用；只读 `task.get` 返回任务及已产生的 Take，区分未选片的输出槽与真正 OUTPUT_MISSING。 |
| 语义观察只看首个 ShotPlan，误判整组视频超长 | 按 Take 的版本化分组身份与 source windows 提供完整镜头组、逐镜意图和相关资产参考；旧单镜头输入路径保持可用。 |
| Worker 的 COMPLETED 答复带有 unresolved_questions，直到运行时提交才失败 | 在 Worker 响应协议阶段验证，保留原答并给同一 Worker 类型化反馈。事实未知项可放入 findings/deliverable，未完成工作则如实返回 PARTIAL。 |
| Leader 要求单一 Worker 同时执行多个独立组，却没有表达可并发的 WorkOrder | 补清当前执行器的运行能力说明，由 Leader 根据依赖重新拆分。实际计划 revision 3 选择每组独立委派；实现没有规定固定组数。 |
| 无人值守服务先等待现有 Worker 完成，导致其他已就绪委派尚未落盘 | 优先派发当前第一个就绪 DELEGATION，再由已有执行器并发推进。仍按 PlanGraph 判断依赖，每次只落盘一个 Leader 动作，不越过排在前面的 Tool 或用户停靠点。新增集成用例先复现失败，修复后确认独立工作可一起推进、依赖工作等待前置完成。 |
| 本地参考图比例超出 H3 范围，准备阶段未做适配 | 在 Provider Data URI 编码时只补背景边，合法图片保持原始字节，更新旧编码缓存。两张实际图片分别变为 391×976、1536×615；本地逐像素验证确认原图区域完整保留。预检记录原始及发送尺寸，实际适配 PNG 和请求体保存到 trace。 |
| 同一 Worker 交替重查第 007、009 组，之前一组的观察正文在下一步丢失 | 按 Take 折叠最新检查报告，保留每组 summary、observations、issues 和精确引用；逐帧长路径留在持久化报告。真实旧历史重建后能同时读到两组结论，原始报告未变。旧 Worker 在修复加载前已以“部分细节未保留”结束，不能把它的完成归功于修复。 |
| 全组核对读到 14 份 ProviderRun 的完整 Prompt/引用，单次输入升到约 30 万字符；已有检查又没有只读取回路径 | H3 列表的模型投影改为运行索引，新增 `route.get_h3_run` 精确读取及 `video.get_take_inspection` 读取已有报告。用同一历史和进度列表复算，工作集从 253,768 降至 22,520 字符；同一 Worker 重启后单次输入由 99,503 降至 29,259 tokens。列表原始结果保留，不把完整历史删除伪装为压缩。 |

修复只改变必要的执行、交接和观察行为。没有重生成首组、改变剧本/分镜/批准资产，或用 Mock 替代真实媒体。测试中的 Fake Provider 与真实案例目录分开保存。

首组六项修复后的相关回归为 294 个通过。补齐并发、图片比例、按 Take 观察交接、运行索引与精确读取修复后，最终相关回归为 309 个通过，完整后端 Pyright 为 0 errors、0 warnings。查询未保存报告也按 Take 保留明确的空结果，后续新报告会替换该状态；不会把“尚未查询”和“已确认没有报告”混为一谈。相应结果分别保存在两个运行目录的 `final-regressions.log` 和 `final-pyright.log`，不将重叠用例累计为额外测试数。这些结果是所列后端测试范围的结论，不代表已经验证最终桌面播放、时间线编辑或导出。

## Trace 与复现

首组目录：`output/agent-video-generation/math-2min-real-h3-20260908-001/`。

| 内容 | 文件 |
| --- | --- |
| 原项目、Run、目标和输入检查 | `session.json`、`preflight.json`、`database-before.sqlite3`、`project-before.json` |
| 实际语言输入、原答、流、用量、角色和上下文参数 | `model_calls/NNNNN/` |
| 实际 HTTP 方法、URL、请求体、响应、状态与耗时 | `provider_calls/NNNNN/`；请求头中的凭据不写入记录 |
| 每次恢复和运行事件 | `process-*.log`、`events.jsonl` |
| 计划、动作、Worker、Tool 参数与结果 | `plans/`、`actions/`、`work_orders/`、`tool_turns/` |
| 持久化任务、ProviderRun、Take、视觉观察 | `tasks/`、`provider_runs/`、`takes/`、`video_inspections/` |
| 每个修复的前文件、源码差异、观察原因和检查日志 | `fixes/001-*` 至 `fixes/006-*` |
| 可重复计算的当前用量和执行核对 | `inspect_execution.py`、`assessment.json`、`model-call-metrics.json` |
| 操作员播放/画面/转写观察 | `first-video-contact-sheet.jpg`、`first-video-audio.wav`、`first-video-transcript.json` |

后续目录沿用相同记录结构，新增 `fixes/007-parallel-work-order-contract/`、`fixes/008-unattended-ready-delegations/`、`fixes/009-h3-reference-image-aspect-ratio/`、`fixes/010-per-take-inspection-working-set/` 和 `fixes/011-scoped-run-and-inspection-reads/`。第 009 项中的 `local-verification.json` 与两张 `*-padded-input.png` 保存补边验证，`provider_inputs/` 保存实际编码发送的适配图。已回收媒体目录见 `math-2min-real-h3-20260908-002/MEDIA.md`，机器可读汇总为 `media-summary.json`；`summarize_media.py --transcribe` 只读取已落盘 Take，使用本地 FFmpeg 和已缓存 Whisper，保留逐 Take 观察、抽帧及音频文件，不修改项目、选片状态或 Agent 检查结论。

执行器入口：[validate_agent_video_generation_run.py](../backend/validate_agent_video_generation_run.py)。与准备阶段一样，运行器通过产品的 create/replan/advance API 推进；不会直接调用内容 Tool 伪装成 Agent 的决策。HTTP 记录器观察相同的实际请求，未改变 Provider 提交/恢复语义。

```sh
backend/.venv/bin/python output/agent-video-generation/math-2min-real-h3-20260908-002/inspect_execution.py
```

首组 `assessment.json` 为其完成时的历史快照；其脚本若再次读取同一项目，会包含后来追加的任务，因此后续汇总使用第二目录，首组快照保持原记录。

首组完成时共记录 65 次语言模型调用，全部返回 usage，累计输入 1,576,018 tokens；没有发现图片编码进入模型文本。各角色的单次输入如下：

| 角色 | 调用数 | 输入 tokens 中位数 | 输入 tokens 最大值 |
| --- | ---: | ---: | ---: |
| Leader | 15 | 22,937 | 24,764 |
| Worker | 48 | 25,084 | 34,003 |
| 媒体观察 | 2 | 26,801.5 | 29,666 |

累计 token 包含开发发现、失败和恢复，不作为稳定生产成本基线。尤其首组早期的等待轮询仍触发语言模型，修复后由执行器继续同一远端任务，需要结合后续执行记录观察稳定阶段的实际调用数。运行中的调用尚未返回 usage 时显示缺失，最终以完整记录重新计算；不把未知用量算成零。

## 后续 Run 的最终上下文统计

第二个 Run 共记录 238 次语言调用，237 次收到 usage；调用 `00159` 在开发中断时未收到 usage，保留为未知。已知累计输入为 4,837,268 tokens，输出为 123,893 tokens。没有在模型文本中发现图片 data URI 编码；媒体观察模型仍通过正常的多模态图片输入接收抽帧与参考图片。

| 角色 | 调用数 | 获得 usage | 输入 tokens 中位数 | 输入 tokens 最大值 |
| --- | ---: | ---: | ---: | ---: |
| Leader | 9 | 9 | 24,243 | 28,519 |
| Worker | 209 | 209 | 17,281 | 99,503 |
| 媒体观察 Tool 内的语言调用 | 20 | 19 | 30,053 | 34,476 |

运行列表膨胀主要出现在 `00168–00170`。索引投影加载后，`00171–00238` 的 58 次 Worker 调用输入中位数为 22,278.5、最大值为 33,173 tokens；2 次 Leader 最大 26,389，8 次媒体观察最大 34,476。该区间主要执行覆盖核对和观察，与之前的生成提交工作不同，因此不把两段累计用量或时间作受控成本对照。

上下文曲线：[PNG](../output/agent-video-generation/math-2min-real-h3-20260908-002/context-input-tokens.png)、[可缩放 SVG](../output/agent-video-generation/math-2min-real-h3-20260908-002/context-input-tokens.svg)。数据来自 `model-call-metrics.json`，图上按角色连接真实输入 token 点，缺失 usage 不估算。`plot_context_usage.py` 使用已缓存的 ReportLab 导出 SVG，PNG 使用已缓存 Sharp 转换；没有安装或下载新的依赖。

## 案例展示边界

剧本、分镜、资产和准备阶段的历史仍分别位于 [核心资产记录](46-phase30-core-asset-plan-and-reference-pipeline.md)、[成熟实现调研](47-video-generation-reuse-and-execution.md) 和 [准备实验记录](48-pre-h3-agent-case-trace.md) 所指目录。本阶段在其后追加实际 H3 请求、视频和开发修复，能够展示从内容准备到真实回收的完整过程。存在用户供图和开发者修复参与，应据实说明，不能描述为从零一次无人干预成功。
