# 当前 H3 生成组的准备与恢复

`route.prepare_h3_group` 处理当前分镜中的一个生成组：读取当前内容，必要时更新对应生成计划，解析已选择的实际参考，编译一次提示词，保存 Route，按调用意图交给 H3 任务。Agent 不再手工传递多个文档版本、素材 ID 和完整 Prompt。现有 ProjectDocument、GenerationPlan、Route 与 Task 继续承担各自状态，operation 只记录进度和引用。

```json
{
  "project_id": "当前项目 ID",
  "request": {
    "shot_id": "本组任一当前镜头 ID",
    "enqueue": true
  }
}
```

`shot_id`、`clip_group_id` 和 `operation_id` 三选一。`enqueue` 默认为 false；准备操作可能调用所选提示词编译服务，视频只在明确设置 true 后入队。默认 `compile_mode: "LANGUAGE"` 沿用 Astra/xhigh；可选官方编译见 [H3 Context IR](h3-context-ir-adapter.md)。图片、视频与声音 Provider 保持既有配置。

默认引用来自当前镜头已选择、可用的生成参考，保留具体素材、用途和实际顺序；DRAFT 状态本身不阻止使用。明确提供 `media_inputs` 时以调用给出的选择为准。混合了互不兼容的首尾帧与参考模式时返回具体问题，不替用户猜测创意路线。

2026-09-12 增加参考用途贯通：`ShotReferenceCue.image_reference_role` 和 `H3MediaInput.image_reference_role` 复用现有图像用途。身份、服装等可复用内容仍建立 Subject；`COMPOSITION` 或 `LAYOUT_GUIDE` 指定的现场构图、动作初态、关键帧图保留 Picture。组入口与单镜路线使用相同用途，不再把每张 reference image 都变成一个新主体。`prepare_h3_group` 的 `input_strategy` 返回实际路线、选择来源、每张图的用途和能力边界；默认从已选择的媒体角色判断可执行路线，不新增语言决策轮，也不把“已有角色卡”判断为“现场站位已准备好”。需要改变视觉方案时，Agent 可读取当前组候选，并在本次调用显式选择路线和有序媒体。

REFERENCE 中的构图图是软指导，可以与身份、场景卡并用；关键帧图的格数由动作需要决定，图中各格表示先后状态，不应成为成片拼图。硬 `FRAME_FIRST` 要求一张已具备身份、空间和物件初态的完整画面；当前适配器禁止 `first_frame/last_frame` 与任何 reference 输入混用。两者不能仅靠改提示词互换。

新 `video.extract_frame` 从一个 Take 的实际本地文件提取完整分辨率 PNG，登记为当前可用的 DRAFT 参考资产。默认 `time_seconds` 是源视频时间；带 `shot_id` 时是该 Take 对应镜头时窗的局部秒，带精确 `clip_id/timeline_id` 时是本次采用位置的局部秒，换算包含裁切、变速与倒放。返回解码帧的实际 PTS、源时窗、资产引用、可直接交给 `image.enqueue` 的 `image_reference_input`，以及软构图用途的 `h3_media_input`。相同来源、区间和说明重复调用复用已有图片。HTTP 入口为 `POST /projects/{project_id}/agent-tools/extract-frame`。

取帧不调用 Provider、不选用 Take，也不声称通过视觉检查；图片是底层视频帧，不包含编辑器字幕、叠层、裁切、翻转或调色合成。实际帧可以交给现有图像工具派生下一镜的现场图；源画面错脸、错道具或越轴时不应直接继承。后续目标图是否适合软参考或硬首帧仍由具体镜头决定。本轮能力用本地真实解码和 Fake 编译验证，没有新增真实媒体调用，因此尚不宣称成片质量收益。

保存的操作返回 `operation_id`、生成计划 document/version、`generation_unit_id`、`route_plan_id`、`task_ids` 和实际状态。Tool receipt 的操作完成只表示本次调用已返回，`WAITING_MEDIA` 仍表示视频任务等待执行或结果，不能当成视频制作完成。

2026-09-12 同步当前 [AceData H3 视频 API](https://github.com/AceDataCloud/MiniMaxAPI/blob/main/docs/videos.md) 的输出选项：首次准备可选 `resolution: "768P" | "2K"` 和 `aspect_ratio`。固定画幅包括 21:9、16:9、4:3、1:1、3:4、9:16；REFERENCE 还可使用 adaptive，纯文本使用固定画幅，硬首尾帧路线省略画幅或使用 adaptive 并遵循实际帧图比例。未指定时使用当前组分辨率或 768P、当前项目画幅；恢复 operation 沿用其保存值，不改变已有任务。单镜 Prompt / Route / Probe 使用相同适配规则。一次生成覆盖值不会改写项目成片画幅，旧任务和保存请求继续兼容。

分辨率、画幅进入实际 Prompt 输入、路线、任务和 Provider 请求，界面按当前配置与请求显示；旧风格备注中的画幅不覆盖本次选择。下载校验去掉“只能竖屏、短边 700–820”的假限制，继续检查实际解码、尺寸、可确定的固定或帧输入比例、时长、镜头时窗及音轨。API 未声明每个档位的精确像素映射，因此不把未知像素规范写成通过。若配置费率只对应 768P，2K 路线和任务估价为未提供；供应商实际用量单独保留。本轮通过 Fake 传输、重启复用及本地媒体校验，尚未据此证明 2K 画质或收益。

同日补齐成片出口：`timeline.export_editorial` 省略宽高时，读取当前实际采用视频的解码尺寸，按项目画幅建立不缩小这些源画面的画布；未采用素材、隐藏轨与 PNG 图版不会把默认尺寸放大。UI 默认“跟随素材”，明确选择 720p / 1080p 等尺寸仍按选择输出。没有采用视频时才回退到画幅默认尺寸。旧 Manifest 新建时同样跟随选中 Take，保存 `OutputSpec.follow_source_dimensions` 以便后续重建继续跟随；历史或明确指定的尺寸保持原值。导出回执与技术检查记录真实像素，原文件保持不变；“保留尺寸”不等同于已经验证生成内容的清晰度或质量。

```json
{
  "project_id": "原项目 ID",
  "request": {
    "operation_id": "已返回的 operation_id",
    "enqueue": true
  }
}
```

恢复时沿用保存的创意输入与编译模式；`route.get_h3_group_operation` 只读取当前状态，不提交任务。相同组、内容、媒体与编译选择会复用已有操作、Prompt 和任务；配置错误修复后可以继续已有步骤。语言编译失败需要明确 `retry_failed_step: true`，已完成的媒体任务和提交未知的任务不会盲目重发。

H3 Worker 的 `SUBMITTING` 同时包含“输入已准备”和“正在提交”两个时间点，保存的 ProviderRun 用 `submission_attempt_state` 区分：`PREPARED` 仅表示本地请求及引用已准备，重启后可以继续同一任务；在真正调用视频 Provider 的 submit 之前，先持久化 `STARTED`。重启时，`STARTED` 或缺少标记的旧记录仍按 `SUBMIT_UNKNOWN` 对账，不能仅因 `submitted_at` 为空而推断没有提交。明确的 `PREPARED` 不会因正常重启要求用户手工对账，也不重新上传或编译。

当前组内容、时窗、相关制作输入或媒体变化时，旧操作保留旧结果并报告 `INPUTS_CHANGED`；用当前 shot/group 发起新的准备。其他组修改和共享 GenerationPlan 换版不使内容未变的组重复编译。不同组可以独立准备，相同组的并发调用共享结果。

返工可以带 `source_take_id` 和 `source_repair_id`。新操作为受影响组创建新候选 Route，保留旧 Take；若只是需要新候选且编译输入未变，可以复用已保存 Prompt。拆镜之后，原 Take 与后继镜头的关联由实际修复记录的来源 Take 和受影响镜头证明。这是整组新候选生成，不是视频 inpainting。

默认编译的顶层字段顺序与真实媒体标签由代码组装，文本只扩写当前组。超过 H3 长度上限时最多修复选中的可压缩正文段，最终仍检查时窗、全部镜头、对白和引用。原始模型输出、修复输入与最后 Prompt 保留在 Trace；任务提交使用这份通过校验的保存结果。

2026-09-11 的本地验收覆盖：服务重启后继续、并发去重、配置修复、失败重试、其他组修改、本组输入变化、实际 DRAFT 引用、返工与拆镜来源、正式 H3 任务入队、提交未知保护，以及官方提示词任务等待和恢复。H3 Worker 与组操作的相关测试共 45 项通过，包括准备后重启继续原任务、真实提交前标记已落盘、已接受但响应丢失后的对账，以及旧记录的保守恢复。

同日按授权对校园案例做必要真实验收：源库无 WAL，以 `mode=ro&immutable=1` 读取 `project-974ca837e45e` 到隔离库，源项目和已采用素材不变。`shot-lunch-01` 对应当前 3 镜组，使用 8 张明确选用的图片（实际为 6 张 DRAFT、2 张 APPROVED）。一次 `prepare_h3_group(enqueue=false)` 真实使用 Astra/xhigh：初稿 7871 字符，超长后仅修复详细正文段，最终 6913 字符通过合同并保存 Route。总耗时 367.54 秒，2 次内部语言请求合计 61,846 tokens；这是本例观测值，尚不能表明编译速度已达到目标或证明相对收益。

复用该 operation 的 `enqueue=true` 创建唯一 H3 任务 `task-9c7d948d2fe44df5aecb9b733b53d541`，Provider 任务 `5ac847b6-e20f-4a73-b311-afbcd9da5f2e` 已成功回收本地 Take；媒体阶段没有再次调用语言模型。一次提交耗时 15.22 秒，Provider 创建至完成时间相差 494 秒，本地首次提交至成功查询并落盘约 537 秒。文件为 10.13 秒、768×1344、24fps，FFmpeg 全流解码成功，有音轨；这不代表已经听审。Provider 实际报告 `total_seconds=10`、`input_seconds=0`、`output_seconds=10`、`input_image_count=8`，音频秒数和 token 用量未提供，实际账单也未提供。

验收期间暴露并修复了准备完成后重启误报未知的缺陷：隔离脚本有明确只执行 `_prepare`、从未调用 submit 的记录，保留该记录后恢复原任务，没有新增任务重试；普通旧任务仍遵守上述对账规则。源案例数据库及其采用集合未改，新候选没有自动采用。完整输入、原文输出、单段修复、Provider 用量和过程记录位于 `output/mature-product-acceptance/h3-compile/`；视频位于该目录的 `media/project-974ca837e45e/takes/cg-lunch-01/h3-task-9c7d948d2fe44df5aecb9b733b53d541.mp4`。

回收后只做了一次 Astra/xhigh 语义画面观察：范围 0–10.13 秒，覆盖组内 3 镜，以 18 帧接触表加 8 张当前引用作为 9 个图像输入，最后采样帧为 9.942 秒。耗时 245.66 秒，实际 45,940 tokens（输入 34,804、输出 11,136，其中推理 8,804）；`semantic_status=COMPLETE` 只表示报告完成，`audio_semantics_observed=false`。没有提供组外采用片段，因此未检查跨组衔接，更不能由静帧判断对白内容、声线和口型同步。

该候选技术链路完成，但内容质量未通过：

- 4.502–6.941 秒学生近景朝画右；7.129 秒后的阿姨反打也朝画右，与已声明的同轴交流关系冲突。报告将其列为 HIGH 空间连续性问题。
- 餐盘在近景呈一长三小分格并缺少可辨米饭，偏离参考与设计；菜勺形态、停勺动作、取卡手侧和首镜校长覆盖也有偏差。手侧判断与未抽到的动作保持其证据限制。
- 实际组时长比设计 9.5 秒长 0.63 秒，第二处切点落在 6.941–7.129 秒，早于计划 7.5 秒。应结合真实对白与后续取卡边界选择剪辑点，不直接按设计窗假定镜头已经准确落点。
- 人工对照参考图后发现主角外观更接近同装配角，属于身份混淆疑点。该次模型报告称林一参考不可读，未完成其脸部比较；本地原 PNG 可读，当前装配顺序与 Trace 的 image_map 一致，离线客户端序列化也保持图像字节。历史 Trace 没有保存当次远端收包图像，因此远端处理原因未知，不将这次未比较解释成源素材损坏或身份合格。
- 近景价牌可见“133 / 165 / 102”，与编译 Prompt 中留给后期图版的空白牌面意图不符。报告未将“后期文字尚未合成”当作生成故障；人工复核仍应检查源画数字是否妨碍后期替换，不能直接视作可用空白底板。

最小修复建议是先保留候选和当前已采用素材：检查可用源余量以处理时长和切点，按真实时窗处理准确文字图版；身份或对话轴线若无可用替代素材，再为受影响组准备新候选。身份参考应清楚并保留有依据的角色可见区别，减少无关主体竞争，不自动增加多图或重做全片。本次没有执行这些重生成，也没有将媒体成功回收当作质量收益或稳定性基线。

观察全文、来源快照与实际用量在 `video-inspection.json`、`inspection-summary.json` 和 `inspection-trace/`，提交与查询过程在 `video-events.jsonl`，准备后重启的精确恢复记录在 `restart-before-submit-recovery.json`。官方 H3 Context IR 本轮仍仅做 Fake 验证，未执行真实鉴权或扩写；Provider 缺失的用量、账单或内容判断不能用本地价格估算补作事实。
