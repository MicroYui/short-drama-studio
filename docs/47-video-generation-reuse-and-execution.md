# 视频生成：成熟实现参考与片段级执行交接

更新：2026-09-08。范围是本次校园短剧完成核心资产后的输入编译、视频任务、恢复与后期交接。真实视频质量、耗时和费用留待视频实验验证；本轮没有调用语言、图像或视频生成 Provider。

后续使用真实语言模型和产品 Agent 的准备实验、实际 trace、上下文观察与修复记录见[校园短剧：真实 Agent 的 H3 调用前准备实验](48-pre-h3-agent-case-trace.md)；该实验仍停在实际 H3 调用之前。

已有实现确实参考了 MiniMax Design、MiniMax-H3 和 LTX Desktop。本次重新核对了公开源码、仓库内保存的 Workflow、实际调用链，并用当前案例检查了交接缺口。适合直接复用的部分继续保留原实现；依赖 MiniMax 私有 Hub 的部分通过现有 Agent 和版本化工件适配。

## 来源与复用边界

| 来源 | 核对到的实现 | 本项目如何采用 |
| --- | --- | --- |
| [MiniMax-H3 提示词 Skill](https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/skills/h3-prompt-writing/SKILL.md)及其[全参考规则](https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/skills/h3-prompt-writing/references/ref-en.txt) | 可脱离 Hub 使用；按模式编译音视频描述，保留引用编号、时间与对白原文 | 已有 Provider Skill 翻译与薄适配器直接装载规则；最终输出采用公开 H3 Prompt IR |
| [H3 仓库的任务 Skills](https://github.com/MiniMax-AI/MiniMax-H3/tree/d21241f0a4b3acbb34c97dae47fa417b7065e438/skills) | 8 个任务方法依赖 Hub 工具；包含官方和社区来源，不能统称为官方原创 | 仓库已经保留 31 个上游文件及归属信息，通过 Method adapter 映射当前工具；不实现第二套 Hub |
| 本地保存的 [Design 短剧 Workflow](../backend/short_drama/skills/official/minimax_design/3.0.11/workflows/drama-series/workflow.md)、[视频编译参考](../backend/short_drama/skills/official/minimax_design/3.0.11/workflows/drama-series/reference/h3-video-prompt.md)、[阶段契约](../backend/short_drama/skills/official/minimax_design/3.0.11/workflows/_shared/stage-execution-plan.md) | 一个镜头组对应一个视频工作项；编译者读完整来源，执行者沿用最终 Prompt、有序 refs、时长和声音方向；局部失败沿原工作项恢复 | 对应 `GenerationUnit → route.compile_h3_prompt → H3RoutePlan → GenerationTask`，继续使用同一 ProjectDocument 和 WorkOrder |
| [Design 插件 H3 节点](https://github.com/MiniMax-AI/minimax-desgin-plugin/blob/bef9de2101e9d873619c83553b8f03ebb0c10663/comfyui-plugin/backend/comfy_api_nodes/nodes_minimax_h3.py) | 按图像、视频和音频顺序上传，返回 task id 后轮询；实际需要 Design Gateway 地址及 Hub 登录令牌 | 学习媒体、任务和输出的交接方式；保留当前已经接通的 H3 Provider adapter，不能把节点中的私有网关 URL 当成开放 Provider API |
| [插件任务登记](https://github.com/MiniMax-AI/minimax-desgin-plugin/blob/bef9de2101e9d873619c83553b8f03ebb0c10663/comfyui-plugin/frontend/src/hub/comfy-run-registration.ts)与[输出回传](https://github.com/MiniMax-AI/minimax-desgin-plugin/blob/bef9de2101e9d873619c83553b8f03ebb0c10663/comfyui-plugin/frontend/src/hub/canvas-output-publisher.ts) | 已接受任务由后端持续跟踪，插件关闭后仍保留输出归属 | 当前采用持久化 Task/ProviderRun/Take；ComfyUI 插件整体移植仍按 PRD 放在成片 PoC 之后 |
| [LTX Desktop](https://github.com/Lightricks/LTX-Desktop) 的本地 [timeline](../electron/export/timeline.ts) 与 [audio-mix](../electron/export/audio-mix.ts) | 按源媒体窗口裁切、多轨覆盖及声音混合，交给 FFmpeg 导出 | 编辑与导出代码底座继续复用；现有后端通过 Shot source window 组织片段。LTX 编辑器入口和双向项目桥仍是后续工作 |

本次查询时，H3 与 Design plugin 的公开 main 分别仍为仓库已参考的 `d21241f` 与 `bef9de2`；来源没有被较新提交替代。版本号只用于说明本次读到的内容，不增加项目冻结规则。

[H3 README](https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/README.md) 明确说明 H3-Context-IR 的托管编排未包含在本次公开源码内。当前本地提示词编译器采用公开规则实现适配，不声称复现了该服务内部实现。[Design plugin](https://github.com/MiniMax-AI/minimax-desgin-plugin) 为 GPL-3.0；LTX Desktop 为 Apache-2.0；H3 README 指向其 Community License。已保存的 Design 安装包 Workflow 没有附公开再分发许可，继续按项目既有发布边界处理；本轮没有复制私有前端或接入 LTX 模型。

## 发现并补齐的交接缺口

已有 `GenerationPlan`、多镜头 H3 Task/Take 与 source window，但 `route.compile_h3_prompt` 只接受一个 Shot，且未传入 Agent 已选 Method。完整案例进入多镜头片段时，会失去真正读取整组来源并应用 Design 视频方法的接口。

另外，原来把分镜字段直接拼成最终 Prompt：本次案例纯文字候选有 1/12 超过 7000 字符，加入已批准参考后的候选有 11/12 超限。这是输入编译交接的问题，不是新的剧本失败或 Provider 生成失败。此时需要根据完整事实编译表达，不能截断分镜或台词。

本轮实现：

1. **片段级编译。** 原 Tool 兼容单 Shot，并新增精确的 GenerationPlan document/version/unit 目标。读取该组全部编辑镜头、台词、时间窗、动作与起止状态、制作声音/视觉方向和逐镜可见名单。只读取当前组；前组只提供承接所需的末态。
2. **让已选方法实际参与。** 请求传递 `method_skill_ids`，按 `VISUAL_GEN / VIDEO_PROMPT_COMPILER` 装载现有方法。Design 的连续叙事、空间、参考和声音方法进入输入；最终 API 语法服从公开 H3 Prompt IR，安装包里的示例摄影风格和中文 Hub 骨架不会成为本项目固定模板。
3. **完整来源与输出限额分开。** 当前组来源直接进入语言编译上下文，无需先拼成一个 7000 字符以内的中间提示词。最终输出校验 H3 长度、字段、逐镜时间窗、原文对白和引用标签；超限、漏台词、缺时间窗或引用标签时返回具体错误。这些结构检查不能代替实际画面、声音和导演意图的验证。
4. **最终输入可以直达任务。** 编译结果返回最终 Prompt 和解析后的有序媒体。`H3ShotRouteDirective` 接收 `prompt_override + media_inputs`，保存在现有 RoutePlan；明确选定的候选无需再调用路线语言 Agent。执行和恢复使用已保存输入，不在提交时再次拼接或重写。
5. **保留现有恢复和编辑语义。** Task/ProviderRun 继续保存项目版本、组内镜头和源窗口；模型提交、轮询、下载、未知提交对账沿原 Worker 执行。选择 Take 后按原窗口组织时间线，保持原生对白与环境/动作声；独立配音、BGM、字幕依项目要求决定。

当前 H3 请求编译器仍使用既有的 9:16 竖屏合同。新片段编译入口会在语言调用前报告与 ProductionSpec 不一致的宽高比；其他宽高比需要同步适配 Provider 请求、提示词及校验。

主要实现见 [片段上下文编译](../backend/short_drama/providers/h3_language_prompt.py)、[Service 入口](../backend/short_drama/service.py)、[H3 路线交接](../backend/short_drama/h3_route_planner.py)与[端到端 Workflow](../backend/short_drama/skills/workflows/end_to_end.md)。

## 当前校园案例的本地检查

源项目为 `project-11b4f7a637c3`，Script v7、Storyboard v2、AssetPlan v24。基于原实验最终快照，在临时数据库编译 GenerationPlan；原实验数据库与素材均保持原样。

- 12 个片段组，覆盖 21 个镜头，合计 131 秒；
- 每组引用 5–9 张已经批准的图片，全部通过本地引用解析及当前 H3 输入合同检查；
- 每次语言编译的文本工作集为 33,872–41,452 字符，包含系统指令，不含图片字节，也不等于 token 数；
- 只保存了待编译的完整上下文，没有调用实际语言/图片/视频 Provider，也没有产生新的视频任务；
- 实际语言模型能否在输出长度内忠实编译、H3 的画面和声音质量、以及整集可播放效果，仍需下一次真实运行确认。

输入和复现脚本保存在 `output/video-generation-research-20260908/`：`inspect_case_inputs.py`、`case-inputs/report.json`、逐组 `*-context.json` 与 `*-media.json`。这些是普通本地调研记录，不替代原实验的项目状态。

## 验证记录

新增 [片段编译与交接测试](../backend/tests/test_h3_generation_unit_language.py) 的 12 项检查已通过，覆盖 HTTP 入口、当前方法装载、来源切片、长来源、无效/过期输入、宽高比不匹配、超限输出、镜头顺序、对白所在镜头、有序参考持久化，以及从重启后的 Service 入队。测试使用本地 Fake 语言服务，未提交真实视频。

| 检查 | 命令与结果 |
| --- | --- |
| 后端全量 | 在 `backend/` 执行 `.venv/bin/python -m pytest -q --tb=short`：1449 通过，2 失败；失败项及补跑见下文 |
| 片段与资产工具补跑 | 在 `backend/` 执行 `.venv/bin/python -m pytest -q tests/test_h3_generation_unit_language.py tests/test_asset_agent_tools.py --tb=short`：16 通过，包含全部 12 项新测试 |
| Python 类型 | 在 `backend/` 执行 `.venv/bin/python -m pyright`：0 errors、0 warnings；再把 `.venv/bin` 加入本次进程 PATH，执行 `.venv/bin/python -m pytest -q tests/test_pyright.py --tb=short`：1 通过 |
| API 类型同步 | 使用本地 Python 执行 `backend/export_openapi_schema.py`，再运行本地 `openapi-typescript`；相对本轮开始，只更新了 3 个请求/响应 schema |
| TypeScript | 根目录 `./node_modules/.bin/tsc --noEmit`：通过 |
| 前端与 Electron 构建 | 根目录 `./node_modules/.bin/vite build`：通过 |
| Diff 格式 | 根目录 `git diff --check`：通过 |

首次全量的两项失败都已解决并定向补跑：资产视图测试仍断言早期 `ASSET_REVIEWER` 标题，而此前实现已改为 `ASSET_BUILDER`，因此更新该测试的职责断言；另一个检查直接调用 PATH 中的 `pyright`，改用已安装的项目虚拟环境后通过。没有为此安装或更新依赖。未把定向补跑写成第二次全量运行。

详细日志保存在 `output/video-generation-research-20260908/` 的 `backend-tests.log`、`python-types.log`、`type-gate-rerun.log`、`typescript-check.log` 与 `frontend-build.log`。
