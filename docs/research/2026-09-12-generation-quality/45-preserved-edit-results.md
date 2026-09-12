# 保留图片编辑原始结果与本地重新合成

状态：2026-09-12 batch 12 已以 `3b88316` 本地提交，联合回归与独立代码审查完成；08:24:13 在新后台 PID 63938 恢复《一起签》原调试任务。恢复前 30 个真实图片任务已成功落盘，视频任务为零。代码及 Fake Provider 验证不代表下一张真实图片已经修好，也不代表新的空项目视频前端到端已通过。

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

恢复后两份首批 Worker 实际请求 `model-ea033b142183`（08:24:20）和 `model-457fe2f4770d`（08:24:21）均使用 Astra/xhigh，实际输入分别 73,927 / 73,200 tokens，耗时 11.284 / 11.980 秒。两份均保留视频前停止点，并实际加载原结果保留、本地派生和旧任务无原图不可恢复的方法；目录中可发现 `image.derive_edit_result`，完整 Schema 尚未按需载入。原签复核 t31 候选登记和证据 t19 裁图请求均与暂停前模型输出逐值一致、成功执行一次。首张新任务 `image-task-8f0936068f31` 为不带 mask 的 REFERENCE_GENERATE，raw=null 是预期行为，不能用它证明新 masked 原图保留已实际运行。

08:34:11.037，首张新 masked EDIT `image-task-33a413244a5f` 完成。原 primary 为 `asset-version-5890dc977e57`，mask 为 `asset-version-a35c45cddfad`，feather=8；新候选为 `asset-version-a826f7a3cbf2`，用于 s12 的空白纸面与手腕中间图。任务实际记录 `.provider-output/image-task-33a413244a5f.png`，文件为 1440×2560 PNG、4,548,283 bytes。只读像素核对确认原结果与最终局部合成结果不同，原遮罩中 1,851,854 个完全受保护像素在最终图中零变化。核对记录保存于忽略输出目录 `observations/first-real-preserved-edit.json`。这证明新真实 Provider 结果确实在合成前独立留存，未为核对重发 Provider；它不证明 Provider 原图比最终图更好。

此时新图尚未由产品 QA 检查，也尚未调用 `image.derive_edit_result` 派生。预览能看到两张空白纸、右手裸笔、左手及唯一笔帽；准确文字与后续镜头用途仍待产品处理。不能把中间图或文件留存当作该镜头最终通过。08:36:37.168 原调试任务进入 paused，34 个图片任务、零视频任务和零 Take，准备统一加载下一轮本地采样修复。

## 首次真实派生与后续判断

08:56:51，收尾工单 t48 对上述局部合成图检查为 2.5／不推荐：左臂入画方向错误、南侧纸张移位，笔帽周围还有矩形遮罩拼缝。Agent 随后选择 `PROVIDER_OUTPUT`，08:57:36.199 保存 `asset-version-2428cdc56872`（会议室 v37），t49 Tool 实际耗时 1.11738 秒。新文件与任务 `image-task-33a413244a5f` 留存的 4,548,283 bytes 原图逐字节一致，PNG 为 1440×2560、EXIF 方向 1；派生窗口内图片任务数仍为 35，没有新增 Provider 调用。当前后来增加到 36 个任务属于别的制作操作，不能归到本次派生。

新派生为 DRAFT、`image_task_id=None`，来源保留实际原任务与请求；原任务的输出仍为 `asset-version-a826f7a3cbf2`。返回时和独立读取时 current 均为 `asset-version-7e083c4a5ba7`、approved 均为 `asset-version-d42fba8ad6bb`，镜头和参考集也没有自动引用 v37。核对保存在忽略输出目录 `observations/first-real-derive-edit-result.json`。

09:00:31，t50 对原图仍判断 2.5／不推荐，且正确区分两类问题：原图的帽周木纹自然，笔尖和纸边比局部合成结果完整，说明这些差异来自本地保留边界；但原图自身的左臂仍从左下入画，南侧纸张也已移动，不能仅靠扩大遮罩补出正确臂向。独立实图对照与该判断一致。此次真实调用验证了原结果复用和问题定位，没有把功能跑通误记成该底图已合格。

随后 s01 提供了不同的实际结果：任务 `image-task-9afed9b27894` 的原图派生 `asset-version-84ce65cf3ade`（v39）确有完整小指和圆润指端，旧合成 `asset-version-7e083c4a5ba7` 的遮罩右斜边穿过该轮廓，将其截成尖褐片。09:04:47 的 QA 为 4.6，但推荐范围明确仅是“局部取像素源”，未把全图或缺文字的中间图当作完成品。

Agent 根据本次重新观察的轮廓建立新 mask `asset-version-d37db8a6ab46`，09:06:38 实际 `MASKED_COMPOSITE` 派生 `asset-version-f51918f60fce`（v42），feather=3，耗时 2.628319 秒，复用同一任务的原始像素。独立看图确认完整指端恢复、尖褐片消失、袖腕连接连续；3,606,046 个遮罩外像素变化为零，current 仍为旧 `7e083c4a5ba7`。新图检查尚待完成，准确纸面标题与正文仍是明确的后续步骤；不将手部重合成成功当作该镜头已验收。记录见 `observations/s10-whitepoint-and-s01-raw-repair-review.json`。

09:13:50，该重合成的首次实际QA返回3.8／不推荐，明确唯一缺项是纸面标题和正文尚待恢复，已修好的五指、袖腕和接触关系可以保留，不要求再生手部。09:14:32，Agent仅作本地平面合成，形成v44 `asset-version-bcadd22eb794`；09:17:21 QA为4.3／推荐，限定为第1镜0秒静态动作参考。独立查看整图与纸面裁图，确认手指完整，纸内文字和双空栏已经出现，但白色贴片及周边灰紫边带仍明显可见。工具复用避免了新一次手部生成，尚不能据此宣称图片局部修复整体成熟。
