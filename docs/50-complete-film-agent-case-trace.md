# 校园短剧：131秒完整成片与Agent后期记录

更新：2026-09-08。项目 `project-11b4f7a637c3` 已完成131秒成片，最终采用 `TimelineManifest v5`，产品 AgentRun `agent-run-cb928c2a7532432c979a10c2582bf5e1` 为 `COMPLETED`。沿用前面剧本、分镜、核心资产与真实视频案例；本阶段追加选片、原生音轨检查、字幕对齐、实际导出和软件修复，未新增H3生成提交。

## 最终交付

- [完整字幕版](../output/agent-asset-validation/math-2min-asset-media-context-v17-20260907-034/_app_data/outputs/short-drama-projects/project-11b4f7a637c3/exports/project-11b4f7a637c3-manifest-v5/final-subtitled.mp4)：1080×1920、30 fps、131.000秒，保留H3原生对白、环境声和动作声，20条字幕按原生音轨时间对齐，并修正中文长句烧录换行。
- [完整清洁版](../output/agent-asset-validation/math-2min-asset-media-context-v17-20260907-034/_app_data/outputs/short-drama-projects/project-11b4f7a637c3/exports/project-11b4f7a637c3-manifest-v5/final-clean.mp4)、[SRT字幕](../output/agent-asset-validation/math-2min-asset-media-context-v17-20260907-034/_app_data/outputs/short-drama-projects/project-11b4f7a637c3/exports/project-11b4f7a637c3-manifest-v5/subtitles.srt)、[ASS字幕](../output/agent-asset-validation/math-2min-asset-media-context-v17-20260907-034/_app_data/outputs/short-drama-projects/project-11b4f7a637c3/exports/project-11b4f7a637c3-manifest-v5/subtitles.ass)。
- [21镜头成片总览](../output/agent-video-finishing/math-2min-complete-film-20260908-001/film-contact-sheet.jpg)、[文件与画面检查记录](../output/agent-video-finishing/math-2min-complete-film-20260908-001/delivery-verification.json)、[播放记录](../output/agent-video-finishing/math-2min-complete-film-20260908-001/browser-playback.json)。
- [从剧本到成片的案例索引](../output/agent-video-finishing/math-2min-complete-film-20260908-001/CASE_INDEX.md)：适合后续PPT挑选原始需求、剧本、分镜、核心素材、Agent动作、失败修复和实际结果。

原始12段H3素材均为768×1344、24 fps，总长135.68秒。成片按21个来源Shot的source windows装配为131秒，导出时缩放、补边并转为30 fps；1080×1920是输出规格，不是原生H3 1080P。最终清洁版与字幕版字节数以 `delivery-verification.json` 为准。没有制作额外TTS、音乐或Master Audio。

## 内容与执行来源

本阶段输入为Script v7、Bible v2、canonical Storyboard v2、ProductionSpec v4、AssetPlan v24、GenerationPlan v3、29份批准核心媒体和12个已回收Take。最终选中12个Take，覆盖全部21个Shot。已保存的Script、Bible、Shot、资产及此前ProductionDocument均未改变；新增的两个 `NATIVE_SUBTITLE_ALIGNMENT` 文档仅记录后期对齐观察。

本阶段使用原产品的create/replan/advance接口推进：选片、timeline.rebuild、audio.inspect_manifest、字幕对齐和timeline.export均有实际Agent Plan/Action或Worker Tool turn。运行记录器不代调内容工具。开发者执行的软件修改、只读来源检查、本地ASR探针、FFmpeg抽帧和浏览器播放另行标注，不能记为Agent自主发现或自动验收。

当前后期Run仍沿用 `method.minimax_design.drama_series`。成熟实现与源码复用边界见[视频生成参考与执行交接](47-video-generation-reuse-and-execution.md)。本轮没有接入ComfyUI或LTX多轨编辑器，完整视频由已有TimelineManifest与FFmpeg导出链完成。

## 版本与真实重跑

| 版本或渲染 | 实际用途与结果 |
| --- | --- |
| manifest-v1 | 选片装配的131秒时间线。完成音轨检查、整集观察与首个完整清洁版；字幕仍为按Shot均分的初始窗口。 |
| manifest-v2 | 新 `audio.align_subtitles` 首次成功，20条字幕使用原生音轨时间，视频与音轨窗口不变。 |
| manifest-v3 | 修复跨Worker报告交接后重跑，新增可精确重读的对齐ProductionDocument。 |
| manifest-v4 | Agent导出参数误用rebuild=true，重新产生默认字幕窗；该误导出的版本保留作失败记录。 |
| manifest-v5 | 导出Tool合同修复后恢复正确对齐，最终锁定此版本导出。 |
| v5换行重渲染 | 实际播放发现长中文句在烧录后被裁切；修复ASS换行后，由同一AgentRun重新导出相同manifest，保持所有时间和内容。旧字幕视频与元数据保存在fixes/007的before-render中。 |

初始音频检查覆盖21个音轨窗口与20句对白，技术检查没有解码、时长或削波失败，但逐句转写暴露了字幕窗口错位。第003组的第二句围观对白原在36.5–40.5秒，对齐后为34.38–37.00秒；第012组的录取核验对白从118.5–124.5秒改为122.26–126.80秒，后续欢呼从125.5–130.5秒改为127.24–130.76秒。字幕仍使用Script原文，未将ASR同音、数字或繁简差异改进剧本。

最终字幕报告为 `production-document-f45440409820` / `production-document-version-6c9eb8733585`，绑定输入v4并明确输出v5。报告包含逐cue前后时间、文字、匹配量、每段ASR原文、媒体不变量和完整观察路径；完整字符时间与WAV另存于报告引用的本地目录。时间对齐属于本地ASR的近似观察，不是声纹或口型鉴定。

## 实跑暴露的问题与修复

| 问题 | 修复与可核对记录 |
| --- | --- |
| H3列表的原Tool结果携带全部图片编码，使WorkOrder超过432MiB | `route.list_h3_runs` v2返回紧凑索引，完整Provider详情仍可精确读取；存量Worker缓存恢复前先保存原始结果。14条实际索引为13,226 bytes。见fixes/001。 |
| 已选的独立Take在工作集中丢失，Agent交替重选001/002 | 按Take ID保留selected_shot_ids与当前状态，处理重叠替换；修复后第003–012组各选一次完成交接。此项增加必要状态，不宣称降低单次token。见fixes/002。 |
| timeline.rebuild把准备后执行revision与canonical创作revision混比 | 确认21个Shot内容除revision外相同后，时间线采用与GenerationPlan一致的当前执行revision；真实内容/版本变更仍拒绝。见fixes/003。 |
| 原生音轨缺少字幕时间修复Tool | 增加本地attention token时间对齐与跨Shot同源连续音频读取。首次实跑的Whisper长度参数错误也保留，修正后重跑。见fixes/004。 |
| 对齐明细只有前一Tool结果和文件路径，下个Worker无法精确重读 | 同次项目写入保存版本化对齐ProductionDocument，交回document/version refs，通过已有artifact.get_production_document读取单份正文。此前因此误触发的重复整集观察没有从trace删掉。见fixes/005。 |
| 导出附带重建覆盖已编辑字幕 | Agent-facing timeline.export v2只渲染当前版本，可用manifest_id锁定；重建仍是独立Tool。旧REST显式重建保留。见fixes/006。 |
| 容器检查通过但长中文烧录字幕左右裁切 | 显式ASS换行，真实FFmpeg像素测试验证多行和左右边距。播放与修复前后图直接确认问题及结果。见fixes/007。 |

[修复前长字幕](../output/agent-video-finishing/math-2min-complete-film-20260908-001/fixes/007-cjk-caption-wrapping/before-render/subtitle-long-line.jpg)与[修复后长字幕](../output/agent-video-finishing/math-2min-complete-film-20260908-001/fixes/007-cjk-caption-wrapping/after-long-caption.jpg)使用同一84.5秒画面。

相关回归按各修复日志分别为：索引/选片150项，revision集成4项，音频与对齐25项，报告交接13项，导出保护17项，最终换行渲染3项。用例范围有重叠，不相加作为唯一测试总数。完整后端Pyright为0 errors、0 warnings，`git diff --check`通过；本阶段未改前端，也未运行或安装JavaScript依赖。初次字幕修复的重规划说明误写34项，实际日志为25项，计数更正保留在fixes/004。

## 模型与上下文记录

本阶段共133次语言调用，131次返回usage；`00101`、`00114`在开发停止时未收到用量，保留为未知。已知输入累计4,986,205 tokens，输出87,384 tokens。没有图片data URI进入模型文本；整集观察通过正常多模态输入读取抽帧。H3 Provider的本阶段请求是恢复启动时的任务查询/凭据验证，不是新视频提交。

| 角色 | 调用数 | 收到usage | 输入tokens中位数 | 输入tokens最大值 |
| --- | ---: | ---: | ---: | ---: |
| Leader | 35 | 35 | 23,972 | 26,974 |
| Worker | 85 | 85 | 52,098 | 72,629 |
| 整集观察工具内的模型 | 13 | 11 | 50,335 | 67,458 |

语言模型为gpt-5.6-sol、reasoning_effort=low；每次请求的输出上限、超时、图片数、角色、上下文字符数、原始输入和usage均见model_calls/NNNNN。汇总为[assessment.json](../output/agent-video-finishing/math-2min-complete-film-20260908-001/assessment.json)与[model-call-metrics.json](../output/agent-video-finishing/math-2min-complete-film-20260908-001/model-call-metrics.json)，[上下文曲线PNG](../output/agent-video-finishing/math-2min-complete-film-20260908-001/context-input-tokens.png)和[SVG](../output/agent-video-finishing/math-2min-complete-film-20260908-001/context-input-tokens.svg)可作PPT素材。

这些数据包含开发、恢复、重复选择、重复审看和失败，不能当作一次稳定生产的费用/延迟基线。前后任务范围不同，也不能仅凭曲线下降宣称受控降本。完整历史留在trace，模型只获得当前工作集和按需工件；本例仍显示大范围选片/整集观察工作会带来较大的单次输入。

## 记录入口与展示边界

记录根目录：`output/agent-video-finishing/math-2min-complete-film-20260908-001/`。session.json、objective.md、database-before.sqlite3和project-before.json保存起点；plans/actions/work_orders/tool_turns保存实际动作；model_calls保存模型输入、原答、流与用量；provider_calls保存去凭据请求记录；manifests、audio_inspections、episode_reviews、exports及fixes保存结果与开发恢复。重新读取统计使用：

```sh
backend/.venv/bin/python output/agent-video-finishing/math-2min-complete-film-20260908-001/inspect_finishing.py
```

素材、剧本、分镜、生成准备和H3执行的历史入口见[CASE_INDEX.md](../output/agent-video-finishing/math-2min-complete-film-20260908-001/CASE_INDEX.md)。早期各阶段记录格式不同，原始trace和项目版本仍保留；不声称每一个早期调用都有与本阶段相同的完整usage字段。

最终成片保留餐盘初态、计算器接触、协议/通知书交接和加菜动作等生成连续性瑕疵。部分观察将食堂侧通道机位判为换场；批准参考本身有侧通道，因此该模型判断不能直接当作已经独立确认的异地场景事实。基本故事和结尾闭环成立，但不能写为所有镜头毫无偏差。Take原始QA状态没有为了结束实验统一改写为通过。

本案例是用户补图、确认与开发者修复参与下的真实后端制片闭环，不是从一句话开始、一次无干预成功。最终文件的播放与字幕排版由开发者另行检查；产品Agent已实际执行语义工件与导出流程，尚不等于已经接通LTX编辑器或完成一体化桌面体验。
