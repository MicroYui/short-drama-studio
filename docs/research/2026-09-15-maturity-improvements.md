# 系统成熟度改进实施记录

按照 [研究报告](2026-09-15-system-maturity-review.md) 的四批顺序推进，四批已于 2026-09-15 完成实现与当前工作树验收。基于 main 的现有工作树，保留已有改动，未提交或推送；本轮未重新打包 macOS 安装包，旧包的历史验收记录不代表已经包含本轮能力。

## 第一批：内容与任务正确性

已实现：

- H3 取消、准备写回和提交开始使用同一短状态边界。慢上传期间仍能取消；迟到结果不覆盖取消；已开始或未知提交保留对账。
- 素材归属与文件可用性分开验证。未采用的缺失媒体可留在素材库；导出只检查当前时间线的实际输出轨。所需媒体缺失会给出时间线、轨道、片段与时间位置。
- 场次草稿按集保留，切页/切集不丢失。保存只确认提交的快照，期间新输入保留并更新基线；文本草稿可由当前会话恢复。
- 桌面工作区首次打开后在后端恢复中/停止时继续挂载，提供重连状态条。

验证：H3 新增 7 项确定性并发回归，既有相邻检查 49 项；媒体新增 13 项，连同相邻保存/导出/镜头检查共 39 项；场次草稿 7 项脚本检查；相关 Pyright 与 TypeScript 通过。媒体测试包含实际本地 FFmpeg 导出。

实际界面验收使用隔离数据库与合成媒体，未调用模型：

- 剧本 → 镜头 → 剧本后草稿保留。
- 切到另一集仍显示未保存统计，点击提示返回原场次。
- 保存请求延迟 3 秒时继续输入；回执后新文字仍保留为待保存草稿。
- 以桌面状态接口适配器依次发出 restarting / dead / alive，剧本草稿及未发送聊天文字保持；重连按钮生效。该项验证原生 App 渲染路径，未通过杀死用户的桌面进程制造故障。
- 后续实际停止、再启动隔离后端：编辑器与未发送聊天文字持续保留，后端恢复后连接状态恢复。该项使用隔离服务，不停止用户已有的制作任务。

验收夹具：[server.py](/Users/liyifan/Documents/ChatGPT/ai短剧调研/output/system-maturity-implementation-2026-09-15/server.py)、[reconnect.html](/Users/liyifan/Documents/ChatGPT/ai短剧调研/output/system-maturity-implementation-2026-09-15/reconnect.html)。

## 第二批：修改反馈与后期交付

已实现：

- 编辑器在没有人工改动时应用 Agent 新修订，并保留有效选区、播放头和缩放；有待保存内容时暂停自动保存，保留人工改动并合并相关字段。
- 导出记录持久保存，支持查看最近任务、取消和按当前剪辑重新导出。取消等待本次 Node/FFmpeg 退出；服务重启后原运行明确标为中断，不保持虚假“正在导出”。
- 预览与导出共享声音淡入淡出包络；拆分片段保持原曲线。最终混音只在超出满幅时对所有声道统一衰减，保留左右声道比例。
- 角色资产详情可以试听、导入或录制声音参考，并绑定或解除当前角色的声音。
- 界面实测发现剪辑器与制作助手的同级 React key 重复，现分别命名；持续刷新后只保留一个制作助手。

验证：导出生命周期新增 5 项本地真实进程/持久化回归；编辑器同步 5 项、导出状态等前端相邻检查通过；声音 6 项真实 PCM/MP4 检查通过。过载输入峰值 1.600024，统一增益 0.593741，导出 PCM 峰值 0.949982、最终 AAC 解码峰值 0.996994；左右 RMS 比 1.98275（输入目标 2）。见 [声音实测](../../output/system-maturity-review-2026-09-15/audio/audio-measurements.json)。

实际界面：

- Agent 新字幕同步后，原字幕选区仍有效。
- 自动保存途中后台修改另一条字幕，页面保留人工文字并显示合并入口；点击后人工第一条字幕与后台第二条字幕都保留，保存成功。
- 声音参考文件上传、试听、解除、重新选择绑定通过，实际音频元素可解码 3 秒内容。录音硬件权限与真实麦克风未实测。
- 从页面完成 25 fps 导出，实测 MP4 为 320×570、25 fps、12 秒且有音轨；切到另一集没有串入记录，返回原集仍显示结果。见 [导出记录](../../output/system-maturity-implementation-2026-09-15/ui-export-25fps.json) 和 [合并后的剪辑](../../output/system-maturity-implementation-2026-09-15/editorial-merged.json)。

声音边界：整片过载增益在导出混音后计算，实时预览不预扫描整部成片；超载工程的导出总音量可能比实时预览低。这里的峰值余量不是完整广播响度标准化功能。

## 第三批：降低真实返工

已实现：

- `video.inspect_take` 支持明确源区间内的密集采样：最长 3 秒、最高 12 fps、最多 36 个真实源帧；低帧率素材不会补帧凑数量。默认稀疏观察保持原用法。
- 可单独请求最多 4 个关键帧，默认最大边 1280，可保留原尺寸；同时保存所请求时间和实际源帧 PTS。
- 同区间可提取实际 PCM 声音片段，保留声道和源时间映射。静态画面观察不会被写成听审或对白时间判断。
- 已有 `video.extract_frame` 可把实际帧登记为参考素材，继续使用现有参考/剪辑链路；无需再生成一张相似图。
- H3 完成回执、路线索引和单项摘要直接附带当前来源检查；历史 READY 与当前是否仍可执行分别保留。前镜采用范围、引用媒体变化会反映在回执中，不增加固定审批或检查循环。

验证：定向观察新增 10 项真实帧/原尺寸像素/音频时间对齐检查，连同原有检查共 38 项通过；H3 新增 5 项当前来源回归、165 项相关上下文和编译检查通过。

## 第四批：常用创作与迁移

已实现并完成当前工作树验收：

- 中文基本标题使用独立文字片段，可改正文、字号、位置、颜色、开始和持续时长；保留透明背景。高级标题样式尚未接通时明确提示。
- 黑场淡入淡出、同轨相邻片段溶解已接到预览与实际导出；溶解在前片段末尾显示下一片段首帧，切点后正常播放，不改变现有成片时长。暂不支持擦除等高级转场和有第三个画面覆盖的复杂溶解。
- 输出帧率可选 24 / 25 / 30 / 48 / 50 / 60 fps。
- 素材详情显示全项目各集使用位置，区分对象、具体文件、镜头与时间线使用；镜头位置可跨集跳转。
- 缺失媒体可重新定位同一原文件，在保留镜头、字幕、裁切与对象 ID 的前提下更新相关集。
- 项目收集包括多集当前内容、剪辑、Take 来源、本地文件和各集完整制作 Trace，包内媒体使用相对路径。导入不会覆盖已有同 ID 内容，也不会自动恢复原电脑上的运行。

实际验证：标题/转场 5 项检查包含 96 帧颜色、中文像素与透明背景、标题出现/消失的帧边界及最终 MP4；原有 PNG/硬切 2 项回归通过。见 [抽帧对照](../../output/qa/2026-09-15-title-transitions/title-transition-frames.png) 与 [实际样片](../../output/qa/2026-09-15-title-transitions/chinese-title-transitions.mp4)。页面实际创建并修改中文标题、设置黑场淡入和相邻溶解、保存回后端，并成功导出 320×570、25 fps、12 秒视频；修订 11 的最终状态为 completed，见 [组合导出记录](../../output/system-maturity-implementation-2026-09-15/ui-title-transition-export.json)。

素材恢复 9 项真实 FFmpeg 与并发回归通过。页面验收实际移走隔离源文件，再选择原文件重新定位成功：3 个 Take 的路径更新，镜头内容全文一致，剪辑项目除媒体路径替换外全文一致，含字幕、裁切和 ID；修订由 11 更新至 12。界面明确提示更新了 2 集的相关引用、剪辑和已完成内容保留，新路径的 8 秒视频可解码且无播放错误。见 [重新定位验收](../../output/system-maturity-implementation-2026-09-15/ui-relink-acceptance.json)，完整项目快照保存在 [操作前](../../output/system-maturity-implementation-2026-09-15/pre-relink-project.json) 与 [操作后](../../output/system-maturity-implementation-2026-09-15/post-relink-project.json)。

项目包新增 4 项检查通过，包括完整 API 下载、移走源目录后在另一数据库导入两集、重开剪辑并实际输出 1.5 秒 MP4、缺失文件报告和重复导入不覆盖。页面实际收集了 2 集、7 份媒体，显示可下载结果。

迁移边界：远程 URL 保持引用而不自动下载；缺失媒体明确列出。导入恢复当前作品和完整可读记录，原 Provider 请求与运行中的工作不自动续跑。

## 最终联合检查

以下结果对应 2026-09-15 四批改动合并后的当前工作树。8 个新增后端测试文件与禁止 mock 检查合计 57 项通过（14.57 秒）；7 个前端逻辑脚本合计 53 项通过。全后端 Pyright 为 0 errors，TypeScript 检查和 Vite 构建通过，构建包含 Electron main/preload。各批前面的相邻回归数字保留其原始范围，不与这次联合检查累加为全量测试结果。

后端命令在 `backend/` 执行，使用已安装的本地环境：

```sh
.venv/bin/python -m pytest tests/test_h3_cancellation.py tests/test_h3_completion_sources.py tests/test_studio_export_lifecycle.py tests/test_studio_missing_media.py tests/test_video_detail.py tests/test_studio_visual_export.py tests/test_studio_project_package.py tests/test_studio_media_recovery.py tests/test_no_mock_usage.py -q
.venv/bin/pyright
```

前端命令在仓库根目录执行：

```sh
node --test scripts/test-studio-scene-drafts.mjs scripts/test-studio-editor-refresh.mjs scripts/test-studio-export-status.mjs scripts/test-studio-activity-refresh.mjs scripts/test-studio-editor-selection.mjs scripts/test-studio-media-library.mjs scripts/test-studio-aspect-ratio.mjs
./node_modules/.bin/tsc --noEmit
./node_modules/.bin/vite build
```

OpenAPI JSON 与前端类型已同步导出状态、定向视频观察、项目包及媒体使用位置接口，包含可选的 `StudioMediaUse.timeline_id`。检查与生成均复用本地依赖，未安装新依赖。

## 实际作品验收边界

本记录中的确定性测试、真实本地渲染与合成片段证明工程行为，不等同于新一轮 AI 整片质量结果。本轮未调用付费 Provider，未恢复先前已停止的真实制作任务，也没有进行新的整片质量或稳定性复跑。现有作品与实际制作过程保留。

麦克风硬件录音未实测；macOS 安装包未重新打包，本轮新增能力以当前工作树验收为准。基本标题、黑场淡入淡出和相邻溶解已经验证，高级视觉效果仍有上述限制；可选新增视频编辑 Provider 尚未集成。
