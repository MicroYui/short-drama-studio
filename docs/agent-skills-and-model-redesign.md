# Agent、Skills、模型与实时进度重构方案

> 日期：2026-09-11
> 状态：本方案的运行职责、方法加载、模型配置、方法详情与真实文字增量链路已实现。2026-09-11 按实际调用入口、离线测试和本地 HTTP/React 检查更新；全产品验收和真实运行结果以交付清单为准。镜头编辑以 [局部剪辑与时长规则](shot-editing-and-timing.md) 为准。
> 产品依据：[产品页面方案](product-interface-spec.md)、[用户修改记录](product-redesign-notes.md)。

## 0. 当前完成情况

| 内容 | 核对结果 | 实际入口或依据 |
| --- | --- | --- |
| 模型与推理强度 | 已完成。语言默认统一为 `gpt-6-astra / xhigh`，请求体实际发送该配置 | `language_defaults.py`、`language_models.py`、`providers/openai_compatible_language_client.py`；Leader/Worker 经同一客户端 resolver 创建 |
| 运行职责与输出协议分离 | 已完成。共同原则、Leader、Worker 与结构化输出协议拆成独立 Markdown，并由运行时读取 | `prompts/runtime/{common,leader,worker,leader_protocol,worker_protocol}.md`；`production_manager.py` 的两个运行提示词入口 |
| 方法元数据、选择与共享说明去重 | 已完成。11 个方法使用独立 manifest，按角色和阶段读取相关片段；多个方法共享的前言/后记按来源路径只加载一次。用途名称及新 ID 已启用，旧 ID 读取时兼容解析 | `skills/loader.py`、`skills/models.py`、`production_profiles.py`、`skill_ids.py`；不是对所有章节正文进行全局去重 |
| 本地经验进入实际提示词 | 已接通。11 个方法的全部默认角色与阶段覆盖均使用本地有效章节，保留具体专业判断和正常失败例；去私有工具、固定确认流程与能力假定，POST 不误载分镜职责 | 各方法 `manifest.json`、`references/runtime-adaptation.md`、`references/production-method.md`；短剧另有故事、编译、后期专段；来源原文保留 |
| Skill 正文统一排版及目录清理 | 已完成。11 个方法统一为用途、输入、方法、交付、检查、来源与加载；删除 22 份重复/闲置文件及 25 个空目录，保留方法片段和出处 | [当前方法目录](../backend/short_drama/skills/README.md)、`skills/methods/*/SKILL.md` |
| 产品内方法详情 | 已完成。可查看正文、来源与按职责/阶段加载的片段，区分项目偏好和本次 Worker 实际选用 | `studio_methods.py`、`GET /studio/methods/{id}`、`StudioMethodDetail.tsx`，设置与制作任务入口 |
| 实时更新 | 逐场保存、实际任务事件、内容刷新与供应商真实文字增量均已接通。未解析完整的内容仅显示为生成中草稿 | `studio_generation_draft.py`、`model.draft`、`StudioGenerationDraft.tsx`、`/studio/projects/{project_id}/events`；非流式返回不伪造增量 |
| 普通产品 Trace | 已完成模型输入/输出、实际配置、耗时和供应商用量记录，并可导出；保留失败和恢复事实 | `studio_trace.py`、`observability.py`、`studio_trace_export.py` |
| 本文涉及的编辑桥 | 已接通，采用后续确认的单镜头局部剪辑与整集时间线合并方案 | [镜头编辑与时长](shot-editing-and-timing.md)、[交付清单](product-delivery-checklist.md) |

2026-09-09 离线重跑四份相关测试，共 **65 项通过**：`test_production_method_skill_loader.py`、`test_production_skill_loader.py`、`test_minimax_design_workflow_integration.py`、`test_language_provider.py`。它们验证已实现的加载和请求行为，不代表未接通的界面已通过验收。

重新读取既有验收记录：仅剧本任务的 13 次调用均为 Astra/xhigh；成片任务 Trace 中 36 份完整请求快照也全部使用此配置，其中 20 次包含 Leader 提示词、16 次包含 Worker 提示词，并有 36 份供应商用量记录。该次核对没有重新调用模型。

原目录树是职责归属建议，没有逐个照搬：当前方法使用 `methods/drama_series`、`brand_promo`、`music_video` 等用途名称；上游原文和实际使用的译文集中在 `sources/`。旧方法 ID 在 `skill_ids.py` 解析为新 ID，不保留重复的方法目录；旧 Trace 不改写。检查职责继续位于共享角色/阶段方法中，没有单独的 `runtime/verifier.md`。Leader 可按实际职责合并或并行工作，简单任务不强制拆为 Planner、Builder、Verifier；检查角色按实际需要使用。

## 1. 目标与保留基础

让用户按自然语言目标工作，在剧本、镜头和成片中查看结果；分镜设计与视频结果同处一镜，资产统一可见，Agent 使用清楚、适量的专业方法，并把实际动作与产生内容持续显示出来。当前 AgentRun、ProductionManager、WorkOrder、ToolRegistry、Provider 和 ProjectDocument 已完成实际成片案例，继续作为基础。

页面数量不对应固定 Agent 数量，也不要求强制流水线。现有阶段和任务切片仍用于限定每次工作与模型输入；不为了界面变化重写已工作的执行器。

## 2. 重构前的问题定位

以下为实施前的定位，源码行号和测量只对应当时状态；当前结论以第 0 节为准。

| 范围 | 重构前代码事实 | 调整方向 |
| --- | --- | --- |
| 按阶段加载 | production_manager.py:5355–5363、6269–6281 已明确传角色和 Stage；skills/models.py:113–117 优先阶段片段 | 保留并验证这一基础，不重复建设阶段隔离 |
| 重复说明 | skills/loader.py:402–431 为各方法重复包通用前言/后记，production_profiles.py:222–233 再合并 | 通用原则只注入一次，方法仅贡献当前任务的专业内容 |
| 文档与执行不一致 | loader.py:589 保存方法正文，但方法编译器只取 description 与角色片段 | 文档明确实际加载的入口，本地经验迁入真正执行的片段 |
| 难读元数据 | drama_series/SKILL.md:8–10 以巨长单行 JSON 保存角色/阶段映射，loader.py:93–100 按单行解析 | 独立、标准缩进的 manifest.json；SKILL.md 专注清楚的说明 |
| 无关实现进入角色 | shared/official_task_method_role.md:9–17 混入 Hub、Canvas、ComfyUI 映射 | 移到工具适配和来源说明，清除已取消的画布约定 |
| 兼容与活动角色混排 | 16 份共享角色、48 份格式角色平铺，部分 Critic/Repair 仅供旧记录兼容 | 活动入口清楚；旧角色放兼容映射，不全部作为默认方法 |
| 模型默认 | openai_compatible_language_client.py:76 为 gpt-5.6-sol，:153 的 reasoning.effort 为 low | 统一配置 gpt-6-astra / xhigh，所有调用和显示读取相同配置 |
| 流式只到后端 | 语言客户端内部累计 response.output_text.delta，最后返回完整文本；界面没有接收该增量 | 分开生成中草稿与已保存内容事件，驱动右侧及正文 |

重构前离线编译 PLANNER / ORCHESTRATION：只选短剧方法为 7,950 字符，另加品牌宣传方法为 10,693 字符；共享前言与后记分别重复两次。这只是当时组合的字符测量，不是当前 token 用量或模型效果结论。

## 3. 建议的文件与职责组织

```text
prompts/runtime/
  leader.md                 总目标、范围、调度与交付
  worker.md                 完成本次任务与工件交接
  verifier.md               在需要检查时评估实际内容
skills/methods/
  script/                   故事、场次、对白、时长与来源保真
  storyboard/               景别、机位、动作、轴线、生成组
  references/               人物/场景/道具、视图、引用与连续性
  video/                    表演、镜间衔接及生成后的实际观察
  audio-and-editing/        声音、字幕、节奏、时间线与交付
skills/formats/              短剧、动画、口播、广告/MV等必要补充
skills/providers/           H3、图片等已接入 Provider 的编译方法
skills/sources/             上游原文、翻译、来源与本地经验索引
```

这是内容归属建议，不要求一次移动所有目录。先用现有 loader 接口兼容，再按迁移表调整活动入口；旧实验仍能解析原有 Skill ID。运行职责属于提示词，Provider 编译属于具体调用，创作方法不再承担产品页面、运行时重试或 Hub 模拟说明。

当前 11 个方法正文已统一采用以下结构：

1. 用途与何时使用。
2. 需要读取的输入和当前内容。
3. 核心制作判断和做法。
4. 交付内容及与现有工件的关系。
5. 必要检查和具体例子。
6. 来源与本地经验说明。

元数据已放入独立 manifest.json，列明 ID、适用任务、片段路径和来源，并用 Python 标准 JSON 读取。产品“创作方法”页和制作任务已提供详情：后端读取实际 `production_method_skill` 的正文、来源、角色与阶段片段；前端可按本次 Worker 实际选中的方法查看，项目偏好单独展示。

## 4. 保留、合并与迁移清单

| 现有内容 | 新归属 | 必须保留的价值 |
| --- | --- | --- |
| MiniMax drama-series 与本地剧本经验 | script + 必要体裁补充 | 来源意图、故事因果、铺垫兑现、场次、对白、时长 |
| 分镜、场景关键帧、生成组方法 | storyboard + references | 空间轴线、景别、连续动作；叙事镜头与 Provider 生成组分开 |
| 角色卡、场景四向卡、道具与文字板 | references | 身份与空间一致、合理参考重量、用户供图优先、最少充分引用 |
| H3 prompt 与任务 Skill | video / audio-and-editing + providers/minimax_h3 | 有序引用、原语言对白、原生音轨、时长与模式约束 |
| 广告、MV、动画等上游方法 | 必要任务片段、体裁补充及例子 | 有效创作手法、Master Audio、剪辑与声音组织；按任务选用 |
| 本地补图、语义审图、字幕与后期修复经验 | 对应实际加载的方法片段 | 成功经验进入真实提示词，工程报错修复归 Tool/Provider |
| 通用用户优先级、持续执行、写作风格、交接规则 | prompts/runtime | 一处维护，不随每个方法重复注入 |
| 旧角色、Hub/Canvas 操作与上游固定确认流程 | 兼容入口或 sources | 旧记录可查；当前流程不默认注入过时指令 |

整理时逐项登记“原文件 → 新片段 → 加载入口”，确认有效经验已进入运行片段后再移走原活动入口。保留来源文件和 License；不将本地方法降级为只能照搬上游，也不保留与用户意图冲突的强制流程。

## 5. 一次模型请求如何组合

```text
运行共同原则（一次）
+ 本轮 Leader / Worker / 必要检查职责
+ 当前目标、项目/集/对象范围、输出合同
+ 已选制片方法的相关片段
+ 必要的体裁与 Provider 补充
+ 本次所需工件、最近状态/错误、未解决决定
```

以稳定片段 ID 去重，记录实际加载清单和字符数。完整 Trace 留在存储中，模型输入按当前任务重建；不把所有页面内容、所有上游 Workflow 或前序 Worker 全部历史放进每次请求。

当前 Leader 和 Worker 的长 runtime instructions 中含有 AssetPlan 修复、具体工具行为等细节。按责任迁回对应方法或 Tool 文档，输出 schema、真实工具名称和交接字段继续兼容已有合同，不因精简文案破坏调用。

## 6. 角色提示词的推荐写法

参考 [OpenAI 官方 Model guidance](https://developers.openai.com/api/docs/guides/latest-model#prompting-best-practices)，采用清楚的目标与职责、明确用户指令优先级、持续完成已委托工作、简洁进度和与任务相称的检查。下面为本项目自行整理的草案，不是复制上游长提示词。

### 共同原则

> 根据用户当前目标和已有项目内容推进。能从上下文确定的细节自行处理；只有缺失信息会实质改变结果时才询问。用户最新要求优先于方法建议。保留用户已经生效的修改，修改前读取相关当前内容。用简短、具体的话说明正在处理什么和已完成什么；输出必须来自实际结果。已授权的工作持续推进；要求完整剧本时，剧本完成就结束并回复，要求完整视频才继续制片。达到本次目标后不自行追加下游工作，明确要求确认的内容按要求停靠。

### Leader

> 负责本次目标的整体完成。根据完整语义判断要交付什么以及何时完成，不从页面、关键词或固定阶段推导停止点。新消息定向修改时结合原任务与最新意图更新目标与后续工作，保护用户已生效修改。明确范围、已有内容和剩余工作，按真实依赖选择工具或分派独立任务。交接足够的输入、目的和输出要求，让执行者在范围内自主完成。收到结果后更新项目与下一步安排；只处理影响当前目标的问题，不凭空增加审查轮次。对用户展示实际行动和交付情况。

### Worker

> 完成本次具体任务。使用提供的当前内容和必要专业方法，自行选择合适的工具，交付可使用的结果与准确引用。发现局部问题时在范围内修复；遇到会改变目标或越过任务范围的问题时回报负责人。保存完整场次或媒体结果后发出进度，不能以计划或尚未完成的输出冒充完成。

### 需要检查时的职责

> 根据本次目标检查真实文稿或媒体，说明具体问题及其影响。能够完成的普通修正直接处理；确实影响上游目标的问题返回负责人。检查与任务相称，不要求无关的统一模板，也不把每个结果强制送入固定循环。

上述自然语言职责与现有结构化输出合同配套使用。方法选择和是否并行由任务语义决定；模型更换不要求引入另一套多 Agent 框架。

分镜、资产和视频方法共同维护正文引用：按实际需要选择具体媒体，写清在何镜、何时段取用身份、服装、空间、文字或其他内容。读取并保留用户已经调整的绑定；将语义引用交给编译器解析为当前有序媒体和 Provider 编号，不靠同名文本替换来维持关系。正文位置、产品保存及实际图片预览已接通，见 [镜头文案与资产引用](shot-asset-reference-interaction.md)。

## 7. GPT-6 Astra / xhigh 配置方案

用户指定的目标明确为 gpt-6-astra，reasoning.effort 为 xhigh。实际读取的 [OpenAI 模型指南](https://developers.openai.com/api/docs/guides/latest-model#migration-quickstart)确认这一模型 ID 和 Responses 用法；本项目已经走 Responses，迁移时保留该路径并遵循其参数支持。

| 触点 | 具体调整 |
| --- | --- |
| language_models.py / providers/language_config.py | 一个配置来源持有 model 与 reasoning_effort；沿用现有 URL/key 配置，保持旧设置可读 |
| openai_compatible_language_client.py | 请求使用配置的模型与 xhigh；不保留硬编码 low；Astra 请求不传不支持的采样字段 |
| service.py → ProductionManager | Leader/Worker 已共享客户端 resolver，直接沿用，避免各角色维护另一套模型默认 |
| 视觉观察、比较、编译和前端设置 | 活跃响应和显示读取实际模型配置，清除过时硬编码；不把图片/视频模型一并改成 Astra |
| observability / 实验 recorder | 记录实际 model、effort、上下文、输出限制和供应商 usage；旧实验数据保留原值 |

普通产品调用现已记录 `model.input`、`model.started`、`model.output` 与 `provider.language_generation`，包含实际模型、推理强度、完整文字输入/输出、字符数、用量、耗时及首个文本增量耗时；缺失的供应商用量不能按零或猜测值报告。

真实代理调用已经验证：`output/product-delivery/script-only-acceptance.json` 为 13 次，`agent-film-acceptance.json` 及 `production-trace.zip` 为 36 次，均为 Astra/xhigh。成片任务初次检查误入旧 Manifest 路径，输入峰值 109,647 tokens；修复后续跑 9 次，峰值 26,218 tokens。保留这个失败与恢复差异，不声称所有调用从一开始就保持较小上下文，也不据此声称完成稳定性实验。官方额外异步工具能力不与模型升级强绑定。

## 8. 实时正文与任务更新

当前产品入口为 `StudioWorkspace.tsx`。它通过 `backendFetch` 读取 SSE，按游标重连，并依据 `content.updated`、`assets.updated` 等事件刷新内容；Agent 动作与结果持续呈现在右侧。真实仅剧本任务已验证每个完整场次保存后出现，无需等待整个任务结束。

实际文本 delta 已从语言客户端 `text_delta_observer` 接到 `studio_generation_draft.py`，写出 `model.draft` Trace/事件并在 `StudioGenerationDraft` 中显示。草稿仅投影结构化创作字段，带调用及任务标识；正式正文仍等待完整解析和保存。Fake SSE 经实际 HTTP 和 React 页面验证多次文字增长时，正式场次仍为空；非流式 JSON 响应不会被伪装成逐字生成。

| 事件 | 正文 | 右侧 |
| --- | --- | --- |
| 动作开始或进度变化 | 标明相关内容处理中 | 显示动作、对象、最近结果 |
| 场次保存 | 新增/局部更新该场及目录 | 显示该场已完成并可定位 |
| 实际文本 delta | 只更新生成中草稿 | 当前动作保持真实 |
| 分镜或媒体保存 | 刷新受影响条目和预览 | 更新完成项 |
| 时间线或导出完成 | 刷新成片状态和文件入口 | 提供结果及制作记录 |

事件带对象标识、范围和必要变化；不重复发送完整项目给模型。未保存编辑不会被远端刷新覆盖，阅读位置不被自动跳转。完成结构化解析才将草稿转成当前正文。

## 9. LTX 与无版本界面的最小正确性

`StudioEditor` 已通过适配层复用 LTX 组件，素材与剪辑保存回本项目后端；原上游 localStorage 项目不作为产品另一份内容真相。

本集维护一份后端 EditorialTimeline。单镜头按钮打开只包含当前采用位置的画面、关联声音和字幕的局部视图，从 0 秒计时；保存后合并回本集并顺移后续内容，成片按钮打开全片。LTX 编辑字段持久化、当前剪辑预览和真实 FFmpeg 导出已验证，详见后续确认的 [局部剪辑与时长规则](shot-editing-and-timing.md)。

保存只更新当前对象。使用内部修改计数或时间检查避免晚到写入覆盖；用户不管理这些字段。不相交修改直接合并，冲突时保留用户内容、读取最新内容继续处理，不生成永久历史分支。编辑器普通撤销继续使用上游能力。

## 10. Trace 与发布会 PPT

用户明确保留制作 Trace，后续用于产品发布会。继续保存现有案例索引与原始文件，产品提供一个可导出的制作过程包：

- 项目目标、剧本、分镜、素材及当前成片索引。
- 按实际时间排列的 Agent 动作、输入范围、结果与媒体链接。
- 实际模型、推理强度、上下文规模、调用耗时和供应商用量。
- 用户补图、关键修改、开发修复与重新运行的事实说明。

包内可同时提供易读 Markdown 索引和原始结构化记录，供后续挑选发布会素材。当前不生成 PPT，不构造额外证据链，不将调试总耗时或费用误写成稳定生产基线。

## 11. 验证与剩余工作

第 0 节记录前次状态核对通过的 65 项检查；目录清理和命名调整的检查记录在 `output/skill-cleanup/`。2026-09-11 已补齐方法详情和真实文字增量链路，`test_studio_method_activity_drafts.py` 的 5 项检查及本地 HTTP/React 交互验证通过；前端类型检查和构建通过。当前整产品验收及真实调用统计由 [交付清单](product-delivery-checklist.md) 统一记录。

本轮新增实际加载回归遍历 11 个方法的全部角色与阶段组合，检查组装后实际输入中的本地章节、专业知识、正常失败例与职责隔离；所有 `role_source_sections`、`stage_role_source_sections` 和 `role_references` 均不再直接载入上游私有运行正文。上游原文、作者与来源仍在 sources 和 manifest 保留。广告的传播证明、MV 的主音乐/歌词时间与空间字体、短剧的摄影/表演/连续性，以及动画、品牌、产品、剪纸等体裁工艺均有可执行判断。资产规划、制作和检查按各自职责加载，不再把整份旧资产流程复制给三种角色。

相关回归还覆盖单一 H3 输出格式、确定性字段顺序、原文 Trace、超长字段局部修复、导演嵌套调用的方法/阶段继承和真实采用边界。方法详情直接显示这些实际片段，11 个概览同步说明本地适配与可选参考策略。当前方法和 Prompt 测试为本地无收费验证；必要真实 H3 编译、回收和一次画面观察另见 [H3 生成组准备](h3-group-operation.md)，候选发现的身份疑点和空间/道具问题如实保留，不把测试通过或技术回收等同于视频质量提升。

验证原则继续采用原方案：

1. 用现有 Skill loader 测试核对片段选择、共享说明仅一次、关键本地经验实际注入、旧 Skill ID 可读。
2. 用现有 FakeSession 检查 model/xhigh 的真实请求体、流式解析与记录一致性；Leader/Worker 共用配置。
3. 用已存在的场次数据模拟增量保存，检查正文自动出现、任务同步、用户输入和作用范围不被覆盖。
4. 用已有视频完成 LTX 导入、单镜头局部剪辑、裁切/拆分/字幕/音量保存、重开、播放与导出检查；重复使用和已移除镜头不被默认组接复原。
5. 核对 Trace 导出仍能定位原始剧本、素材、调用和最终视频。

测试与实际功能检查通过后再交付，不因迁模型扩建质量竞赛、强制审批或独立稳定性实验。未验证的代理能力和上游导出差异如实记录并在对应实现中解决。

## 12. 2026-09-11 生成准备与提示词编译

H3 输入保留逐镜事实、对白原文、有序媒体与真实时窗，Provider 标签和顶层字段顺序由代码组装。视频编译角色不再同时接收 Design 的固定六段骨架与本地 Prompt IR；11 个方法只提供当前体裁需要的动作、声音、字体与连续性知识。超长输出只请求修复可压缩的正文段，保留其余原文；初始输出、修复输入和完整装配结果进入 Trace，最终仍验证全部镜头和对白。

导演建议读取本次 WorkOrder 的方法与阶段，POST 使用实际采用媒体和当前剪辑职责。前镜设计末态明确标为计划；真实前镜边界从当前时间线的采用 Take 和源区间确定，只有匹配当前文件、镜头修订和观察范围的报告才提供实际末态证据。没有这类观察时返回未知，静态观察不代表听审。

`route.prepare_h3_group` 把当前生成组、按需更新的生成计划、输入解析、一次提示词编译、Route 保存与可选任务入队组成可恢复操作，交接稳定的 operation、plan、unit、route 和 task 引用。未变的组可复用保存的 Prompt；其他组或全片计划更新不强制重复扩写。返工关联原 Take 和修复记录，生成受影响组的新候选，不能称为视频局部重绘。详见 [H3 生成组准备](h3-group-operation.md)。

可选官方 `H3_CONTEXT_IR` 与默认 Astra 编译互斥，异步任务等待使用保存的 operation 和下次查询时间恢复；当前没有进行真实收费调用、没有验证官方账户鉴权，也没有宣称质量或速度收益。细节见 [官方编译适配](h3-context-ir-adapter.md)。
