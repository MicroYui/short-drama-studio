# Research 改造交付与验收

实施日期：2026-09-11。依据 [研究总览](00-review-and-recommendations.md) 和 01–08 分报告改造当前产品。主入口、唯一项目内容和目标驱动的 Agent 保持既定设计；重写实际存在问题的执行、上下文、参考、编译、观察、局部修复与交互环节。本文区分实现、实际验收和仍需真实样本判断的效果。

## 1. 已落地的改造

| 研究问题 | 当前实现 | 主要代码 |
| --- | --- | --- |
| 工具定义反复发现 | Leader 与 Worker 持久保存已发现 Tool ID；每次使用当前 Registry 定义，恢复后仍可用；按角色加载少量常用工具，长尾继续按需发现 | `agent_runtime/tool_context.py`、`agent_runtime/store.py`、`production_manager.py` |
| 当前工作集重复、读取互相覆盖 | 规范化默认参数，以对象、视图、pointer、offset 区分读取身份；大段旧读取保留精确回读引用；当前请求共享项目读取缓存，Trace 保存原文 | `agent_runtime/tool_context.py`、`agent_runtime/context.py`、`production_manager.py` |
| 同批等待最慢分工、媒体轮询占槽 | 持久执行池逐个回收 Future；就绪依赖及时调度；媒体和 Context IR 等待保存下次查询时间并释放执行槽，不以模型决策代替轮询 | `team_executor.py`、`studio_runtime.py` |
| 暂停后仍有旧 Worker 写入、恢复可能重做 | 暂停先接管下一次决策/Tool 边界，等待已开始的复合 Tool 完成和回执落盘后才标为 paused；同一任务恢复，已完成结果继续使用 | `studio_runtime.py`、`team_executor.py` |
| 单个审阅阻塞整项制作 | 审阅关联实际 WorkOrder、场次、镜头、生成组和依赖闭包；只等待依赖该选择的分工，明确无视觉影响的 `[]` 不扩大到整场 | `studio_review_scope.py`、`studio_changes.py`、`production_manager.py` |
| 剪辑器的“修改这个”丢失具体选区 | 发送时读取当前片段、字幕、图片/音频或空隙，先保存再携带准确采用位置、源时窗与修订；全片/单镜坐标明确，多选不跨时间线；无需先点额外按钮 | `studio_models.py`、`studio_runtime.py`、前端 `studio-editor-selection.ts`、`StudioEditor.tsx`、`StudioAgent.tsx` |
| 选区已知仍反复查工具、读取整片，切换时间线后改错位置 | 剪辑任务首轮提供精确读取定义；按字幕/片段 ID 返回完整的小结果与成片秒数。读取与修改显式指定时间线，保留当前活动时间线；重复 ID 未指定目标时给出具体候选，旧修订拒绝覆盖 | `studio_editorial_selection.py`、`studio_editorial.py`、`studio_shot_editorial.py`、`tools/studio/`、`production_manager.py` |
| DRAFT 参考被丢弃、图版或旧引用误进生成 | 当前明确选择且文件可用的图片直接使用；生成/编辑用途分开；当前对象的 cue 覆盖历史 ReferencePlan 与 canonical fallback；视频及关键帧编译/比较共用当前规则，旧版图片不“复活” | `reference_planner.py`、`video_inspection.py`、`visual_input_quality.py`、`h3_group_operation.py` |
| 局部观察冒充整组检查 | 观察记录明确用途、媒体、版本和实际区间；按镜头实际 source window 观察；比较可覆盖整组及非代表镜，复用报告须匹配当前问题和覆盖范围 | `video_inspection.py`、`take_comparison.py`、`tools/qa/` |
| 设计中的前镜末态被当成实际发生 | H3 编译和嵌套导演任务带入相关已采用媒体的实际边界与可用观察；区分设计、画面事实、音频事实和未知项 | `production_media_context.py`、`providers/h3_language_prompt.py` |
| Skill 同时注入私有工作流与 H3 两种骨架 | 11 个方法的全部角色与阶段指向本地运行片段，保留专业知识、补齐后期方法并移除私有工具、强制确认和固定参数。体裁指引同步当前时间线和局部读取；来源独立保存。Provider 层唯一负责最终格式、标签、顺序和限制 | `skills/methods/`、`skills/shared/`、`skills/formats/`、`skills/providers/minimax_h3/`、`production_profiles.py` |
| 字符数或媒体参数错误导致整段重写 | H3 标签、参考顺序、结构和计数确定处理；只对具体超限章节做必要语言修复；嵌套模型使用当前方法与阶段，真实输入、用量和输出保留 | `providers/h3_language_prompt.py`、`providers/minimax_h3_client.py` |
| 准备、编译、保存路线、提交反复搬运 | 新组合入口 `route.prepare_h3_group` 解析当前组 → 准备有序引用 → 编译一次 → 保存路线 → 按需建立真实 H3 任务；保存 operation 可继续，输入未变复用结果 | `h3_group_operation.py`、`tools/route/`；[使用合同](../../h3-group-operation.md) |
| 导演修复未同步当前分镜与生成输入 | 局部修复同步当前 managed Storyboard 或增量草稿、镜头、生成组时窗及引用；拆镜保留实际采用画面和声音；返回真实准备入口，不进入 Mock retake | `director_content.py`、`service.py`、`reference_planner.py` |
| 采用新 Take 覆盖人工裁切或错用源时间 | 根据实际采用位置映射旧/新源窗口，保留裁切、变速及原声；只更新实际受影响镜头；文件、实际时长与覆盖范围不足时给具体错误 | `studio_take_selection.py`、`service.py` |
| 对齐仍写旧 Manifest、字幕微调需整片回写 | 对当前编辑的裁切/变速/倒放音频对齐，保护文字、样式和无关轨道；现有 `timeline.edit_clips` 支持字幕字段 patch，与声音可一次提交并返回变更回执 | `studio_subtitles.py`、`studio_editorial.py`、`tools/studio/` |
| 跨镜字幕在局部视图消失或保存被截断 | 局部显示实际交集，保存恢复未修改的外侧边界；明确修改只改相应字段；拆分 ID 不与整片已有 cue 冲突，保存检查唯一性 | `studio_shot_editorial.py`、`studio_editorial.py` |
| 错误反馈粗、成功不代表交付文件存在 | 参数错误返回具体 JSON pointer、对象 ID、限制与修复建议；写入返回实际回执。完成时检查明确声称的工件和本地文件，不增加固定 Critic 或创作审批 | `tools/feedback.py`、`agent_runtime/completion.py` |
| 方法只有简介、进度笼统、等待不可见 | 可读方法详情；显示真实 Tool、回执、完成工件数、等待时间和局部审阅；真实文字增量只作生成中草稿，完整结构解析成功后才保存正式正文 | `studio_methods.py`、`studio_activity.py`、`studio_generation_draft.py`、前端对应组件 |

以上代码路径以 `backend/short_drama/` 为根。前端对应 `frontend/features/short-drama/`，未引入新的状态框架或另一份项目内容。

## 2. 借鉴方式和候选能力取舍

- ViMax 的工具生命周期、就绪执行，MiniMax Design 的当前工作项合同、最终 Prompt 与有序引用交接，已改写为本地实现。没有引入完整新 Agent 框架或复制闭源应用前端。
- 即梦、Kling、Flow 的明确参考用途、实际帧与边界复用、局部控制，落到当前 cue、实际媒体上下文、观察与采用操作上。不同 Provider 的输入格式没有混成通用模板。
- H3 Context IR 已实现独立可选适配、缓存、异步查询、提交未知态和恢复；它替代语言编译，不串联两轮扩写。官方端点与凭据独立于当前 AceData H3；本机未验证官方授权及生成收益，默认主链继续语言编译。详见 [适配与限制](../../h3-context-ir-adapter.md)。
- 保留原视频的局部生成编辑、专用表演控制和原生视频理解仍是按实际残余缺陷选择的 Provider 候选。研究未证明应无条件加入这些供应商；当前局部编辑使用真实剪辑，必要重制作使用受影响 H3 组，不把 Mock retake/extend 作为已交付能力。
- 当前 H3 生成组仍为 9:16。设置明确显示这个生成限制，现有 16:9、1:1 项目的剪辑及导出能力保持独立；没有用 UI 选项冒充尚未打通的生成规格。

来源、许可和闭源实现可知边界沿用研究 02–08 与 `skills/sources/`、manifest/NOTICES，不改变上游归属。此次没有把私有 Workflow 的分发许可推断为已解决。

## 3. 真实验收与已发现问题

使用《午饭时间》的隔离副本，只复制项目、已有任务/Take 和实际媒体引用，不复制历史运行中的作业。原项目与媒体保持。真实语言请求使用现有 `gpt-6-astra / xhigh`，未因效率目标降低用户指定模型配置。

### 当前剪辑、局部编辑与导出

第一项任务修改第 8 镜原声音量至 0.8，并将指定字幕延后 0.10 秒，再导出 540×960 完整成片。已完成并回收 **57.11 秒、24 fps** 含字幕视频、无字幕视频及 SRT；实际导出检查通过。首次隔离环境漏带本地语言凭据产生一次 Unauthorized，补齐原配置后恢复同一任务，完整失败记录保留。

这次实际任务暴露字幕时码需要整份时间线回写，于是补入现有精确编辑工具。第二项真实任务将同一原声音量从 0.8 调为 0.7，字幕提前 0.10 秒；Agent 使用一次 `timeline.edit_clips` 同时写入两处修改，随后实际导出、文件检查和目标收尾均成功，没有故障恢复或人工确认。

| 原始记录 | 第一项任务 | 第二项精确编辑任务 |
| --- | --- | --- |
| 提交到完成 | 521.51 秒，含凭据配置恢复 | 201.37 秒 |
| 语言请求完成记录 | 18 条：17 成功、1 Unauthorized 无用量 | 9 条，全部成功 |
| 原始输出仅请求 schema | 4 次，包含不同新工具 | 1 次，首次请求导出工具 |
| 实际工具执行 | 9 次，包含 5 次读取、整片保存 | 5 次：读取、精确修改、读取、导出、文件检查 |
| 最大实际输入 | 51,339 tokens | 25,369 tokens |
| 累计实际输入 | 461,913 tokens，包含缓存输入 | 186,471 tokens，包含缓存输入 |
| 工具失败 | 0 | 0 |

两项要求、scope、起始内容和工程版本并不完全相同，第一项还含凭据恢复，因此这里只列原始数据，不计算受控提速率。第二项唯一新增 schema 请求对应的导出定义随后补入 POST 常用集合；没有额外发起第三次相同任务来宣称调用数一定会减少。

四个实际视频（两次各含字幕/无字幕版）均完整解码通过：540×960、24 fps、57.11 秒、1,370 帧、AAC 48 kHz 双声道。第二次保存按对象 ID 严格只有目标音量与字幕起止时间三处字段变化；其余资产、片段、字幕、任务和 Take 保持一致。第 8 镜实测音量比例为 0.87397，对应期望的 0.7/0.8=0.875；两段无关音频比例约为 1。六组无字幕帧像素一致，5.125/7.333 秒的字幕帧确认字幕开始与结束提前。以上是编辑和媒体处理验收，不是全片听审或审美评分。

### 原生桌面与本地导出

离线打包的 macOS arm64 应用实际启动内置 Python 服务，读取上述隔离副本；两项真实作业的完成结果、方法和 Tool 回执重开后可见。当前成片实际播放并暂停在 `00:00:31:16`，进入本集剪辑后，从原生导出窗口选择「竖屏 · 720p」与字幕，界面依次显示画面渲染、字幕渲染及导出完成。

该次桌面导出 `editorial-export-69961af8ec73` 的输入与第二项任务保存的当前剪辑逐值完全一致，仍为修订 38，没有修改内容。字幕版和无字幕版均完整解码 1,370 帧，720×1280、24 fps、57.11 秒，音轨、尺寸和时长检查通过。原生设置也实际打开了「短剧与连续剧集 → 导演 · 剪辑与交付」专业章节，并显示当前 H3 9:16 生成限制。记录见 `native-acceptance.json` / `validate_native.py`。

新增选区交接已在重新打包的原生应用验证：选中字幕后，直接在右侧要求读取当前选择；实际作业保存了精确字幕 ID、FILM 坐标及修订 38，助手正确回复原文与 5.08–7.28 秒，编辑器在执行期间保持打开。此任务 105.70 秒、5 次语言请求、2 次只读工具、0 工具失败，未修改当前内容。比较使用相同的 Studio 展示投影；此前已完成的 720p 导出记录单列，不把 API 投影字段差异误判为内容变更。记录见 `native-selection-acceptance.json`。这次也暴露了按镜头读取和结果截断造成的额外往返，后续精确 ID 读取修复另行列明，不能把这个旧合同任务的耗时当成修复后的测量。

精确读取修复进入最终桌面包后，再次在原生时间线选中同一字幕、直接发送相同只读要求。实际任务 **27.51 秒、2 次 Astra/xhigh 请求、1 次精确读取、0 次 schema-only、0 次回读、0 次失败或重试**；首轮已携带读取定义，直接传入时间线 ID 和字幕 ID，下一轮收到完整 FILM 时间及局部映射并结束。实际累计输入 48,088 tokens，单次最大 24,118 tokens，读取 Tool 用时 0.315 秒。助手正确答复原文与 5.08–7.28 秒；保存前后的原始数据库文档逐类型、字段及数组顺序比较，277,825 个叶值无变化，不使用 API 投影或忽略字段。记录见 `native-selection-final-acceptance.json`。这是同一小任务的一次修复后观察，不据此推导全产品加速百分比。

最终包还实际打开「广告与 TVC → 方法概览」：专业正文与上游来源可读，来源标签不再显示未渲染的 Markdown 路径，打开详情没有改变方法选择。交付应用留在隔离验收副本的作品页。

### 真实 H3 组合入口

独立副本使用开场 3 镜组、当前明确选择的 8 张可用图片（6 张 DRAFT、2 张 APPROVED），保留原状态。当前 cue 排除 4 个只用于后期图版的对象，且不因历史 ReferencePlan 的旧版本将这些图片重新加入生成。

`prepare_h3_group(enqueue=False)` 已完成真实 Astra/xhigh 编译及路线保存，整个操作 367.54 秒。初稿 7,871 字符超过 H3 7,000 上限，第二次语言请求只改写 `detailed_description`，合成最终 6,913 字符，没有重新走整组规划或修改案例分镜。两次语言请求实际输入 23,197 / 16,435 tokens，耗时 141.15 / 223.76 秒；这仍是可见的耗时，不能把组合入口写成一次瞬时操作或永远一次语言调用。

继续验收复用同一 operation 和已保存 Prompt，只建立一个候选任务，未重新编译。任务 `task-9c7d948d2fe44df5aecb9b733b53d541` 只提交一次，已成功回收 **10.13 秒、768×1344、24 fps、含音轨**的视频并完整解码；媒体等待期间语言调用为 0，operation 变为 `MEDIA_READY`。原项目与已采用 Take 集合保持，候选没有替换当前成片。

随后只执行一次 Astra/xhigh 视觉观察，覆盖这组 3 镜实际源窗口和 0–10.13 秒视频，输入为 18 帧接触表与 8 张当前参考。耗时 245.66 秒，实际输入 34,804、输出 11,136 tokens。报告识别出反打视线/轴线、餐盘形制和米饭状态、菜勺、动作与实际切点等问题；独立对照参考图另有主角身份混淆疑点和不该生成的数字牌。候选没有判为质量合格，也没有自动继续抽卡。音频未做语义听审，静帧观察不证明连续动作或对白对齐；模型对身份参考可读性的疑问亦保留在原报告中。见 `video-result.json`、`video-inspection.json`、`inspection-summary.json` 和有序参考接触表。

身份疑点另行核对了原图片与当前请求装配：林一参考图在本地可读，2560×1440，实际绑定为 H3Picture2；有序引用及离线请求序列化未见错序或占位。历史 Trace 没有远端收图回执，不能据模型一句“不可读”认定素材失效，也不能断言远端处理正确。现已在通用短剧编译方法补入同装多角色的可见身份区别和逐角色引用经验，没有硬编码本案例或强制多生成图片。

### 在集成验收中继续修复

1. 暂停重入可能提前显示 paused：保留原复现 Trace，新增确定性测试证明所有已开始 Future 完成后才确认暂停；恢复不重复写入。
2. 历史参考里同对象的旧图片可能重新进入当前 8 图组：真实编译前的校验暴露，修正当前选择对历史默认值的覆盖，没有篡改案例数据来通过。
3. `agent.read_context` 的 on-demand tag 曾让已读分页不进工作集：使用实际 Registry tag 复现并修复，pointer/offset 不同的页面共同保留。
4. 跨镜字幕的空时间字段被误认成显式裁切，以及多次切片生成重复 ID：具体反例修复后覆盖再次打开、保存和真实 FFmpeg/SRT 导出。
5. 真实后期中完整 EditorProject 的历史读取仍占约 27k tokens：保留当前 clips/subtitles/修订摘要，将明确的 `project` 字段改为同一 snapshot 精确引用。对原请求离线重建，两次后续输入分别从 51,038/51,329 降至 29,337/29,628 个本地估算 tokens，各减少 21,701；43 clips、10 subtitles 和工程回读逐值相同。最新主动读取保持完整。这是离线输入重建结果，不是新的 Provider 用量。
6. H3 本地准备完成后、真正 HTTP 提交前重启被误标为 SUBMIT_UNKNOWN：新增准备/已开始提交标记，只有明确尚未发送的状态可继续；已开始及旧未知记录仍按未知提交恢复，防止重复请求。
7. 编辑器给 Agent 的范围只有镜头/成片，具体片段、字幕和同镜不同采用位置丢失：补齐 `StudioScope.editor_selection`，贯穿发送、保存、作业/后续消息和实际模型目标。新选择合同的 10 项前端用例、37 项后端相关用例及类型检查通过；保存期间切换时间线的请求仍保留发送时目标。设备解锁后补齐了原生字幕选择、直接发送与真实 Agent 读取回复的闭环验收；同镜多次采用、菜单、空隙及异步切换分别由针对性用例验证，未将其全称为手动 UI 验收。
8. 只改视频编译与后期片段仍会让概念、规划和资产角色加载旧私有工作流：继续修复全部 11 个方法、110 份默认角色正文与 770 个有效角色/阶段组合，保留具体专业知识和失败例。实际加载快照与最终组装提示词逐项检查，未检出旧私有工具及强制确认流程；来源目录保持原文，页面概览同步当前加载方式。
9. 最终逐链路复核发现关键帧编译仍读历史 ReferencePlan，可能用旧价牌挤掉当前学生乙：首帧/尾帧/拓扑编译沿用当前生成引用解析，保留明确范围、主画布优先和旧项目兼容；比较工具消费相同的精确图片 ID，并标注真实用途和状态。真实快照 START/END 均为当前 8 图，旧价牌排除；34 项新旧用例及 Pyright 通过，Fake 客户端逐张核对实际发送的图片字节与顺序，没有调用供应商。
10. 原生字幕任务暴露精确结果仍被旧摘要长度截断、镜头局部时间引发回读：`timeline.read_editorial` v2 按精确 ID 返回成片秒数、当前修订与可直接使用的编辑目标；该小结果完整进入下一次输入，仍受总上下文预算约束。剪辑任务预载这一项读取定义，避免一次纯 schema 往返。`timeline.edit_clips` v3 同样接受明确时间线，读写共享重复 ID 消歧与修订检查，操作非活动时间线不切换界面或覆盖其他时间线。71 项相关用例及 Pyright 通过；实际 Runner 的 Fake 客户端用超过旧摘要长度的 2 字幕、3 片段验证 2 次语言决策、1 次读取完成，不把此模拟次数当成真实模型结果。

### 验收记录

主要记录目录：`output/mature-product-acceptance/`。

| 范围 | 记录 |
| --- | --- |
| 原案例、第一任务与当前内容 | `source.json`、`before.json`、`request.json`、`activity-latest.json`、`after-first-task.json` |
| 第二项精确编辑任务 | `request-precise-edit.json`、`before-precise-edit.json`、`activity-precise-latest.json`、`after-precise-edit.json` |
| 实际调用与媒体核对 | `task-metrics.json` / `measure_tasks.py`；`media-validation.json` / `validate_media.py` 与 `media-validation-frames/` |
| 原始实际模型输入/输出与工具事件 | `runtime/outputs/short-drama-projects/project-974ca837e45e/studio-trace/` |
| 媒体结果 | 同项目 `editorial-exports/`；按对象 ID 的修改检查见 `editorial-diff-by-object.json` |
| 暂停问题复现与修复 | `pause-race/`、`harness-regression.txt` |
| 真实 H3 组编译/生成 | `h3-compile/`，单独隔离库，候选不覆盖原作品 |
| 方法实际加载与最终提示词 | `method-runtime-audit/current-summary.json`、各方法 `*.current.json`、`loaded-prompts/inventory.json`；只读检查脚本 `audit_loaded_prompts.py` |
| 后端、类型、构建和桌面 | `backend-final.txt`、`typescript-final.txt`、`frontend-build-final.txt`、`runtime-build.txt`、`native-package.txt`、`native-acceptance.json`、`native-selection-acceptance.json`、`native-selection-final-acceptance.json` |

## 4. 验证与交付边界

最终全量后端回归 **1,898 项全部通过，用时 516.07 秒**，包含既有 Pyright 检查。命令为在 `backend` 目录执行 `PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q --tb=short`。所有业务源码收尾后重新导出 OpenAPI，TypeScript、Vite、内置运行时构建和离线 macOS 打包均通过；最后生成的应用包已完成上面的原生字幕选择、直接交接、真实 Agent 精确读取和方法概览复核。

中间两轮完整回归与最终结果分开保存：较早一轮为 1,817 项通过；全角色方法改写后一轮为 1,859 项通过、5 项旧文案断言失败。后者逐项改为检查当前专业内容，保留重启继承、角色隔离、旧方法 ID 兼容与组合语义，没有通过删测试掩盖行为差异；相关节点与最终全套均通过。初次环境的 pytest 子进程 PATH 缺少已安装 Pyright 也已修正，所有失败日志保留。

最终 `git diff --check` 通过；27 份研究、交付与方法文档的 264 个本地链接有效。未因文档修改重复执行已通过的业务测试。

本轮不声称已证明长期无人干预成功率、整片首次可用率或某个提速百分比。研究中的 1,831 次语言完成记录、93M 输入和 16 小时跨度包含原制作、用户审阅、工程修复与恢复，与这次局部后期任务并非等价样本。已经验证的机制、实际新任务和剩余 Provider 能力边界分别报告。

## 5. 工作区和运行边界

- 2026-09-12 按用户要求将本轮产品代码、测试和研究文档提交到本地 Git，包含此前已存在的相关产品文档修改；`.playwright-mcp/`、`docs/presentation/`、媒体、运行记录和应用包留在本地，不纳入此提交。未推送。
- 使用已有本地 Python 和 JavaScript 依赖，无公共 npm 安装；普通模型配置只保存在独立用户数据目录，不进入应用包或本报告。
- 交付目标为本机 macOS arm64 开发应用，签名、公证、Windows/Linux 和公开分发不属于本轮已验收范围。
- 真实调用用于必要功能闭环验证；原来的长片与 Trace 保留，没有重跑一整部独立稳定性实验。

2026-09-12 用户进一步要求以最终剧本、图片和视频质量为验收依据，详细重审 Tool/Skill 的专业设计，并完成新的全资产、全视频与端到端制作。上面的工程检查、已有素材后期和单个 H3 候选只覆盖各自范围，尚未完成改造后从空项目开始的整条制作。后续工作继续使用成熟产品的公开方法与可合法复用实现，按实际生成质量决定是否重构，不以此报告中的测试数量替代作品验收。
