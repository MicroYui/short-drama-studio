# 保留图片编辑原始结果与本地重新合成

状态：2026-09-12 已暂停《一起签》调试任务并实现 batch 12，联合回归与独立代码审查已完成，待加载运行。此时 30 个真实图片任务已成功落盘，视频任务为零。代码及 Fake Provider 验证不代表下一张真实图片已经修好，也不代表新的空项目视频前端到端已通过。

## 真实问题

《一起签》s01 新候选 `asset-version-1388b6ef4aa3` 使用 `image-task-91469cb1bce1` 的 masked EDIT。原图 `asset-version-933dd3c823a1`、遮罩 `asset-version-0eb6852ff8a6`，本地 feather 为 3。Builder 在新方法加载后强调完整前臂、腕掌和新旧位置，但复用上一轮 QA 的 14 点多边形和箭头，只改了说明。实际成图的指尖沿选区下边截断，旧袖尖沿圈外被保留；3,631,561 个原保护像素零变化。精确观察见 [42](42-plane-qa-handoff.md)。

batch 11 的 `AssetWorkbench._land_provider_output` 将 Provider bytes 写入 staging 后，`_apply_masked_composite` 在同一路径覆盖，再搬至候选文件。该任务只有 staged_path/output_path，没有合成前结果。非零 feather 仅在本地应用，而且只在原选区内混合，不扩大可修改范围；Provider 收到的是原始遮罩。因此不能判断本次 Provider 原图是否已经有完整手指，也无法仅改变本地遮罩重新利用已有像素。必须把这一可观测性与复用缺口和 Provider 自身是否画好分开。

## 公开方法与本地采用

[Adobe 无损编辑](https://helpx.adobe.com/photoshop/using/nondestructive-editing.html)说明保留原始图像数据的编辑方法；[编辑图层遮罩](https://helpx.adobe.com/photoshop/using/editing-layer-masks.html)将图层内容和可显示范围分开，改变遮罩可以重新显露已有像素。这里引用的是一般图层与遮罩原理，不声称已取得 Adobe 私有生成实现。

[ComfyUI ImageCompositeMasked](https://docs.comfy.org/built-in-nodes/ImageCompositeMasked)将 source、destination 和 mask 分开输入；[GrowMask](https://docs.comfy.org/built-in-nodes/GrowMask)提供显式扩大或收缩遮罩。这些公开操作说明修改合成范围可以独立于新一次生成。当前本地已有多边形遮罩工具，本轮不增加每张图必须扩圈的流程，也不要求调用 ComfyUI 服务才能修图。

采用的最小机制是为新 masked EDIT 留下原始 Provider 文件，并提供可选的本地 `image.derive_edit_result`：提取原结果供观察，或用原请求的 base 和明确的新 mask 重合成。旧合成结果、任务计费、当前采用媒体和正文绑定分别保留；新派生仍是 DRAFT，不替 Agent 决定采用。原结果没有留存的历史任务不能从已覆盖的候选倒推恢复，给具体说明且不自行重发 Provider。

还要校正制作方法：有手指截断、残留旧轮廓或接缝时，比较实际新旧轮廓和原 mask。重写描述不等于改变几何；只柔化原选区内边缘也不能恢复圈外丢失内容。先判断已有原结果是否可用，再决定局部重新合成、重新定义选区或实际重新生成。

## 实现与验证边界

新 masked EDIT 在本地合成前将原始 bytes 独立保存，任务记录 `provider_output_path`。`image.derive_edit_result` 明确区分两种选择：`PROVIDER_OUTPUT` 保留原始编码；`MASKED_COMPOSITE` 用原 primary、新选定遮罩和已保存原图生成 PNG。局部重合成检查实际图片尺寸、各输入已转正的 EXIF 方向，以及遮罩的实际 PNG 编码、透明通道或透明表。原 Provider 路径的输入兼容规则不扩大。每次派生保存独立 DRAFT，来源关联原任务与实际输入，不修改原任务、原输出、当前采用、批准状态或镜头绑定，也不产生 Provider 请求。

恢复覆盖“已有原图但尚未完成本地合成”的失败和重启。真实重启回归曾发现启动阶段把所有已提交任务统一转为 SUBMIT_UNKNOWN，误挡住已有原图的任务；本轮对存在已记录原图路径的任务改为 OUTPUT_MISSING / RECOVER_OUTPUT，使原来的 pending advance 可以完成本地恢复。无原图的旧提交未知分支保留。重新合成始终从原 bytes 开始，不能对已羽化结果再次混合；旧 masked 任务如果仅剩无法区分合成前后的 staging，会明确说明无法恢复，不猜测也不重新请求 Provider。

独立代码审查还定位到恢复复用普通生成 finalizer 时会无条件切换 current。新增恢复能力因此必须保留锁内最新采用状态：恢复只修复原文件或补齐该任务输出，不抢回用户后来选择的图片；普通新生成的默认行为保留。这是可复现的代码分支缺口，不声称当前真实案例发生了用户选择丢失。

核心验证使用现有 Fake Provider 返回真实 PNG，检查原 bytes 保留、本地不同 mask 结果、原保护区像素、Provider 调用数、重启恢复、旧数据缺原图的具体反馈，以及派生不自动改当前/批准/镜头。恢复中断的本地落盘必须从确知的原结果单次合成，不能把未合成 staging 当作完成结果，或把已羽化的结果再次混合。实际产品图片验证仍随原调试任务继续；新空项目 E2E 尚未开始。

最终检查：图像编辑与派生、QA/空间观察/平面建议、Skills、Worker 上下文、ProductionManager、Studio Harness 和 no-mock 联合 **382 passed / 213.69 秒**。联合启动后最后补入的恢复选择与 EXIF 两处修复，另由最终 Workbench 36 项及实际 Tool/Service 7 项覆盖，**43 passed / 9.23 秒**；不将重叠测试相加。10 个本批 Python 文件类型检查通过，最后五个核心文件再次检查为 0 errors。OpenAPI/前端类型已生成，TypeScript 与 Vite 前端/Electron 构建通过，五份改动研究文档的本地链接及 diff-check 通过。独立审查提出的两处具体问题已修复并复核，没有以测试数代替实图质量。
