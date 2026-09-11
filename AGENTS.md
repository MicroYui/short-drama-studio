# Short Drama Studio：项目与工程指南

## 产品定义与当前工作

这是单用户本地 AI Agent 视频制片桌面产品。用户用自然语言说明本次交付目标，Agent 自行判断工作与完成边界。主入口为“剧本 / 镜头 / 成片”：同一镜头内同时展示分镜设计、逐时段描述、引用资产和视频结果；固定“项目资产”入口统览所需对象及文件。右侧 Agent 对话与制作任务常驻，实时显示实际动作与完成内容。镜头和成片复用 LTX Desktop 中不依赖 LTX 模型的剪辑功能。

当前权威文档：

- [产品 PRD 与实施顺序](docs/product-prd-and-roadmap.md)
- [产品页面方案](docs/product-interface-spec.md)
- [真实分镜、视频与资产关系](docs/storyboard-shot-and-asset-explainer.md)
- [镜头文案、具体媒体与 @ 引用交互](docs/shot-asset-reference-interaction.md)
- [镜头编辑、单镜头剪辑与三种时长](docs/shot-editing-and-timing.md)
- [用户七条修改与 Trace 保留要求](docs/product-redesign-notes.md)
- [Skills、角色提示词及 Astra xhigh 重构方案](docs/agent-skills-and-model-redesign.md)
- [Agent 持续执行、258k 上下文与子任务管理](docs/agent-harness-context-and-lifecycle.md)
- [131 秒成片与完整制作记录](docs/50-complete-film-agent-case-trace.md)
- [可选 ComfyUI 本机连接与真实验收](docs/comfyui-local-integration.md)

2026-09-09 用户已授权按推荐方案完成整个产品，无需再询问产品方案。采用三个内容入口，镜头以局部视图编辑并同步本集时间线；界面参考 Apple/Microsoft 的信息层级、留白、系统字体与交互细节。语言角色改用 gpt-6-astra / xhigh。完整实现与验收进度见 [产品交付清单](docs/product-delivery-checklist.md)，与当前 goal 一同持续推进。原校园案例已完成真实成片，独立稳定性复跑仍暂缓；文档和原型不代表功能已交付。

## 产品边界

- 主产品不做语义画布、节点连线或独立“创作 / 剪辑”双工作区。内容页不决定 Agent 必须执行的步骤；分镜设计保留完整信息，与相同镜号的视频结果放在一处。
- 用户要求“生成完整剧本”时，Agent 写完剧本即完成任务并回复；要求完整视频时才继续制片。目标完成与用户要求中途确认是两种情况，不要写成所有目标都自动成片，或由正则、关键词、固定阶段路由决定停止点。
- 定向修改后，Agent 说明修改、实际影响和后续动作；是否继续原目标由原任务和新意图共同决定。默认保护人工修改、不相关工作和仍可使用的结果，不要求用户手动逐层同步。
- 项目资产统一展示人物/场景/道具/声音对象与待补项；图片、视频和音频文件在同处另一个视图查看。项目、本集、当前内容是同一清单的观察范围，避免重新分出多份素材状态。
- 镜头正文中的 @ 引用须关联具体对象、媒体、取用用途和时段位置；只读时悬停或聚焦预览实际绑定图片，进入“编辑分镜”后才更换并保存。Agent 默认建立绑定，用户可在光标处输入 @ 选择。正文、引用清单和生成输入维护同一关系，不能仅把名字染色或显示孤立的资产清单；正文回写、换图、解除引用及生成输入校验已接通，历史记录不补写成逐句人工确认。
- 内容直接修改并保存当前结果，不做版本列表、采用新版、历史比较或分支恢复。常规撤销保留；旧 ArtifactRef/version 和实验数据兼容读取，不借此扩建版本系统。
- ProjectDocument、当前内容、素材引用和本集编辑时间线是唯一内容真相。内容页、LTX 和可选 ComfyUI 都是视图或执行入口，不各建一套项目状态。
- 镜头分镜默认只读，明确“编辑分镜 / 保存分镜 / 取消”，切换页面保留未保存草稿。设计时长、该镜可用素材时长、成片采用时长分开；没有视频也显示完整分镜。
- 单镜头剪辑只显示这次使用的画面、关联声音与本镜字幕，从 0 秒计时；保存局部改动并同步成片、顺移后续镜头，取消不写入。多处采用时明确选择使用位置。成片入口展开全片。LTX 前端数据必须回写本项目后端，不能继续把其 localStorage 项目作为另一份内容真相。
- 一个项目可以包含多集，共享人物、场景、道具和声音，各集有独立剧本、分镜、镜头和剪辑。范围、选中引用、按集草稿与定位逻辑继续保留。
- 用户与 Agent 可以并行工作，默认保留用户已生效修改；只合并相关内容，不用运行占位页挡住整个产品。
- 逐场完成内容自动呈现；供应商真实文字增量可展示为生成中草稿。未解析完整的结构化输出不能直接写入正式正文。
- 制作任务展示实际正在做什么。Trace 保留动作、输入与输出、实际模型/推理强度、上下文规模、时间和供应商用量；缺失用量标明未提供。用户明确将这些记录用于产品发布会 PPT，不能因取消内容版本界面而删除。
- 外部图片生成受阻时给出具体所需素材、说明及参考图发送顺序，接收用户补图后继续，不反复卡在 Provider 过滤问题上。

## Agent、Skills 与模型

- 语言 Agent 使用 gpt-6-astra，reasoning.effort 为 xhigh；Leader、Worker 和语言检查角色使用同一个有效配置来源。以实际请求及 Trace 验证生效，不以界面标签代替运行结果。
- 图片、视频、语音各自使用已接入 Provider，不因语言模型更名而替换。当前真实视频主链为 H3。
- Skills 保留成熟制片知识和本地有效经验，整理为运行职责、任务方法、体裁补充、Provider 编译与来源资料。上游原文和本地实际注入内容分开，按本次角色、任务和阶段加载并去重。
- 通用方法的目录、Skill ID 和产品名称按用途命名，例如 `methods/brand_promo`；H3 专用适配保留 `providers/minimax_h3`。上游出处、作者和必要参考集中在 `skills/sources/` 及 manifest/NOTICES 中，改名不改写来源归属。旧方法 ID 通过 `skill_ids.py` 读取兼容，旧 Trace 保持原值；清理与当前入口见 `backend/short_drama/skills/README.md`。
- Skills 约束思考与制作方法，Style 属于项目并允许用户覆盖。Skill 不是 Tool 白名单、固定模板、审批门或强制工作流。
- 制作不设预算门槛、阶段冻结或强制审批。时长账本、总纲和独立检查都为可选；分镜可以直接逐组写入并实时显示。旧规划数值不阻止当前创作，实际 Provider 限制在适配层处理。用户明确要求的文本修改对照与逐项选择继续保留。
- 明确选中的当前可用媒体可以直接用于素材完成登记、参考图集、图像派生、H3 输入和声音绑定；不要求先把 DRAFT 改成 APPROVED。审核记录保留实际判断，缺文件、已拒绝、失效或引用不匹配仍按具体问题处理。原始候选记录不会自动把制作项标为完成。
- 视频可否采用由当前文件与实际内容决定，原来的接口测试用途标签不禁止使用。视频观察工具只提供媒体信息和抽帧观察；原声与对白时间使用音频分析、字幕对齐工具，不能把静态画面当成听审结果。
- 镜头正文可以保存生成参考和剪辑图版，用 `ShotReferenceCue.usage` 明确区分；H3 的图片数量限制只作用于实际生成请求。当前镜头明确选择且文件可用的 DRAFT 图片可以直接使用，不要求再走批准步骤，也不伪造检查结论。视频准备和执行按本次选中的生成组校验，其他镜头修改或全片计划换版不阻挡内容未变的组；本组镜头、时窗或制作输入实际变化时才更新对应输入。
- 用户明确交给 Agent 决定时自主推进；明确要求确认时只停在指定内容。其余基于完整语义判断，不用正则、关键词路由、固定轮次或 Critic 循环代替判断。
- 保留完整 Trace，但正常模型输入只携带当前目标、任务范围、所需工件、最近状态/错误与未解决决定。不要回灌整份项目、全历史或所有 Skill 原文。
- 结构化内容分批保存；AssetPlan 可先保存已确定项，再按依赖追加，新增项与字段修改可以一次合并保存。不同工作项的字段保存按所选对象合并，不用整份计划的版本号阻挡无关结果；对象或制作来源实际改变时保护当前内容。唯一工作项ID可自动解析父项。Worker 对不同场次、镜头、文档、素材任务切片和上下文分页的读取按具体对象及读取范围分别保留，重复读取同一范围更新原记录，避免不同内容互相挤掉后反复回读；共享项目索引不随每个镜头副本重复注入。
- 运行上下文预算为 258,000 tokens，包含输入、输出/推理及 1,024 tokens 封装预留。Leader/Worker 在完整请求输入接近 200k 时整理至 160k 以内；使用随包携带的 o200k 编码计数，估算与 Provider 实际 usage 分开记录，不把字符数当成 token 数。Provider 层也检查嵌套 Tool 的文本请求；视觉 token 仍由 Provider 报告。
- 原始用户要求、最新要求和当前目标摘要保留，Worker 用 working_notes 保存必要判断与下一步。大结果先持久化，再以精确 context_ref 和 JSON pointer 替换；agent.read_context 支持分页回读，Trace 导出包含原文。当前兼容代理尚未验证 responses/compact，不能把它作为运行的必需依赖。
- 产品 Runner 根据 Agent 计划自动推进，默认最多同时执行 4 项就绪分工；新消息只有在交接成功后才确认。暂停/补充消息在下一次决策或 Tool 前接管，已开始的操作结束并落盘。语言连接失败使用同一任务有限退避续跑，已完成媒体操作不盲目重发；无可执行步骤时显示具体原因，不空转。实现和当前验收范围以 Harness 文档为准。

## 复用边界

- LTX Desktop 是应用壳与编辑代码底座，不是本产品的本地模型 Provider。不启用、集成或分发 LTX 模型权重。
- 上游 generation、Retake、Extend、IC-LoRA 模型后端不能宣称为 Agent 能力。需要新媒体的同类意图映射到 H3 或已接入 Provider；纯编辑直接复用。
- MiniMax Design 外层画布、Main Agent/Team Engine、Hub Gateway 没有已确认的公开实现，不复制安装包私有前端代码。当前主产品已取消画布；Agent 继续基于现有 Harness。
- MiniMax-H3 公开 Skills 和已保存 Design Workflow 是方法来源，保留有效知识，整理后按需加载。不要把整个上游文案和本地补丁重复堆入每个角色。
- ComfyUI 是相关媒体中的可选高级入口，不再作为产品页面开发的前置依赖。复用公开插件时保留其 GPL-3.0 License/Notices；Design 安装包 Workflow 的再分发条款在正式发布前处理。
- 当前 App 挂载 StudioWorkspace，StudioEditor 复用 LTX 组件并回写后端；导出走共享 LTX/FFmpeg 运行时。原 Home/GenSpace 的模型工作流没有接到产品入口。实际验收和限制见产品交付清单。
- 本机已复用 MiniMax 管理的 ComfyUI 服务 `http://127.0.0.1:18188`，网页与校园案例桌面配置已连接，真实素材上传、缩放、回收和 Trace 导出已验收。服务须独立保持运行；默认 8188 不可用时先检查已有配置和监听，不直接判断需要重新安装。该服务的节点、依赖及模型不包含在 Studio 应用包内。

## 普通工程原则

- 按普通产品工程和普通实验方法推进，采用满足正确性、可复现性所需的最小机制。
- 文档正常更新，Git 自然保留历史；不设置冻结层级、文档 SHA 锁定、额外审批门或证据链。
- 清楚区分当前实现、已确认决定、待讨论设计和待验证结论。实验得到结果后直接更新相关说明。
- 不主动建设安全审计、对抗评测、隐私框架、密码学证明或独立 QA 产品；只有具体在范围内的风险才增加相称控制。
- 保护已有工作区修改；只修改本次需要的文件。提交、合并和推送分别遵循用户授权。
- 当前任务已授权完整产品实现和必要的真实功能验收。先用已有案例与媒体验证界面及编辑；真实调用只用于目标所需检查，保留实际过程，不扩展为独立稳定性重复实验。

## 当前代码入口

| 领域 | 位置与职责 |
| --- | --- |
| 产品前端 | frontend/App.tsx、frontend/features/short-drama/StudioWorkspace.tsx、StudioEditor.tsx |
| LTX 编辑底座 | frontend/views/VideoEditor.tsx、frontend/types/project-model.ts、frontend/contexts/ProjectContext.tsx |
| 前后端桥 | frontend/lib/backend.ts；后端请求统一走 backendFetch |
| Electron | electron/main.ts、electron/preload.ts；IPC 经 window.electronAPI |
| 产品 API | backend/short_drama_server.py、backend/_routes/studio.py（产品）及 short_drama.py（制片能力） |
| 业务与保存 | backend/short_drama/studio_service.py、studio_runtime.py、studio_editorial.py、studio_export.py，及 service.py / repository.py / models.py |
| Agent | backend/short_drama/production_manager.py、team_executor.py、agent_runtime/ |
| Skills | backend/short_drama/skills/、production_profiles.py |
| Tools | backend/short_drama/tools/；Tools 操作同一项目 |
| 语言模型 | backend/short_drama/providers/openai_compatible_language_client.py、language_config.py |
| 视频与导出 | backend/short_drama/h3_worker.py、providers/、media.py；LTX 导出还涉及 Electron |
| 测试 | backend/tests/；优先复用相关 Fake service/client 和集成测试 |

### 前端

React 18、TypeScript、Tailwind；路径别名 @/* 映射 frontend/*。使用项目现有 React Context 和语义颜色，不引入额外状态管理框架。TypeScript 开启 strict、noUnusedLocals 和 noUnusedParameters。HTTP 请求统一经 backendFetch，IPC 经 preload 暴露的 electronAPI。ComfyUI、LTX 和新页面的数据桥放在明确适配层。

### 后端

产品入口是 Short Drama FastAPI 服务，路由保持薄层，由 ShortDramaService、Repository、ProductionManager、ToolRegistry 与 Provider 实现领域行为。不要把 LTX 上游 AppHandler 架构误当成短剧业务的唯一入口。

上游模块仍遵循 _routes → AppHandler → handlers → services/state，具体见 [backend/architecture.md](backend/architecture.md)。并发采用先读/验证、解锁执行慢操作、再锁内写入，不在锁内做模型请求或重型 IO。异常在边界统一记录，避免重复记录再抛出。

Python 3.13+，依赖定义在 backend/pyproject.toml。类型检查使用 Pyright strict。测试优先现有 Fake 实现和 TestClient；遵守 test_no_mock_usage.py，不引入 unittest.mock。

## 命令与受限依赖环境

公司网络只允许受管理的 Microsoft 源，JavaScript 依赖执行默认离线。不要运行裸 pnpm、corepack、npx、npm install、yarn install 或其他可能下载包管理器/依赖的命令，除非用户明确授权。

项目命令使用 ./scripts/pnpm-project.sh，它解析缓存的固定 pnpm；缓存未命中时按既有 Microsoft 源配置处理，不自行改用公共 registry。优先运行现有本地二进制。

| 用途 | 命令 |
| --- | --- |
| 开发桌面应用 | ./scripts/pnpm-project.sh dev |
| 带调试器开发 | ./scripts/pnpm-project.sh dev:debug |
| TypeScript 检查 | ./node_modules/.bin/tsc --noEmit |
| 前端构建 | ./node_modules/.bin/vite build |
| 本地后端测试 | 在 backend 中执行 .venv/bin/python -m pytest tests/相关测试.py |
| Python 检查 | 使用当前 backend 环境中已安装的 pyright |
| 完整类型检查 | ./scripts/pnpm-project.sh typecheck |
| 全后端测试脚本 | ./scripts/pnpm-project.sh backend:test；此脚本包含 uv sync，先确认依赖可用 |
| 平台构建 | ./scripts/pnpm-project.sh build |

- 缺少本地依赖时说明具体缺项，停止该项检查，不向公共或替代源重试。
- 修改 registry 或包装脚本后运行 node scripts/test-managed-node-registry.mjs，这是本地检查。
- PR 按仓库要求完成 typecheck、backend:test、build:frontend，受限环境可使用等价本地二进制，并如实报告执行命令和未完成项。
- 验证与改动相称；文档和交互方案检查链接、脚本与实际显示，业务改动运行相关测试，编辑功能检查实际播放与导出。检查通过后不无理由扩大或反复重跑。
