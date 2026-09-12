# 固定实际输入的纸面内容合成离线比较

2026-09-12。仅读取已保存的项目文档和文件；全部中间图、输出和脚本在 `/tmp/paper-normalization-study/`。没有 Provider、产品 Tool、项目/候选/采用写入。

## 结论

当前两个样本可以明显收敛，值得作为可选本地纸面内容处理继续设计；不能作为通用照片编辑，也不是成片验收。最佳组使用“源纸局部背景估计 → RGB 除背景并去除实测纸纹噪声 → 原四角透视/原保护区 → 乘到目标纸的实际像素”。保留了原摄影姓名、红改、线条位置；处理不重绘任何字。

s13 v43 的两块灰内框在整图和局部都明显消失，目标纸面的冷白色与原光照仍在。s01 v44 的白色贴片不能单靠改 multiply 解决：原底图有旧经办/复核字和两条线，直接乘墨会产生四栏。最佳 s01 对照先只恢复已观察到的旧印字，再加同源原墨迹；白片和双栏都消失，手指、边界及接触影保留。标题仍被原手指部分遮挡、远景文字仍小，这些固定构图问题没有被此实验修复。

## 实际文件与对比

- `actual-inputs.json`：只读 DB 提取的两份实际结果、base/source 版本及原 source.prompt。
- s01 基准：`asset-version-bcadd22eb794` / v44，base `f51918f60fce`，source `a9fb492dce6b`；原请求 replace。
- s13 基准：`asset-version-785342cc40c4` / v43，base `f93c6507d35e`，source `3adcb6b5365e` 双签摄影和 `d99209b73470` 报价摄影；原请求 multiply、白点 230。
- `s01-refined-comparison.jpg`：左实际基准，中仅归一叠墨（失败：旧栏重复），右明确清除旧印字后归一叠墨。
- `s13-refined-comparison.jpg`：左实际基准，右最终局部归一叠墨。
- `source-ink-details.jpg`：原摄影手写、红划线和“调整报价”与归一后逐位置对比。
- `s01-best-preview.jpg`、`s13-best-preview.jpg`：整图 576×1024 观察。
- 全分辨率最佳输出：`s01-median-clean-target.png`、`s13-median-ink.png`。

实际产品 renderer 重放两个原请求均与已保存基准逐像素相同。所有比较保持原 source_quad/target_quad、图像尺寸、corner 对应和 protected_regions；只改变来源颜色处理与声明的 s01 旧印字恢复。

## 少量比较与采用理由

1. 原模式：s01 replace 带进源纸照明，出现明显白片；s13 固定白点 multiply 仍带进源纸灰度，形成内框。
2. Gaussian 背景除法：s13 已改善；但背景估计会受到文字本身的平均值影响，容易在粗字和线附近引入亮晕，不作为最终方案。
3. 形态闭运算估计源纸：保留细节但估计偏亮，仍有浅矩形；用闭运算直接填 s01 目标旧字又形成亮条，不推荐其直接输出。
4. 最终：源图 31 px median + sigma 3 Gaussian 估计局部纸背景。按 RGB 分通道除背景；实际可见空白源区 x=.20–.35/y=.12–.16 的残差 99 百分位仅作为这次离线去纸纹噪声值（约 1.3–2.1%），不是全局白点或所有纸的常量。保留原墨迹颜色和位置，经现有面积采样 renderer 乘到底图。
5. s01 必要额外步骤：仅已观察旧印字区域，在原 target 内且排除原保护区，用局部闭运算定位暗字/线，1 px 邻域覆盖其抗锯齿，再 Telea 半径 3 px 恢复周围纸色。实际改 2,212 个像素；不是整页抹平，也不是对人物/任意照片自动擦除。最终叠墨仍用原 target。

## 验证和实际边界

已独立看整图、两纸局部、原手写和红改细节。归一后“许岚/赵衡”的摄影笔迹形态、红划线位置、红色正文、细线没有观察到不可接受损失；s13 两页方向与原请求一致。源颜色会随除背景稍变，不能说 RGB 原值保留；红色相对更鲜明。低于本次 1.3–2.1% 纸噪声残差的浅墨会被消除，因此铅笔、压痕、水印、弱灰字、复杂有色纸/渐变图案必须另判断，不能以这两个样本证明通用保真。31 px 不是产品固定值，应相对于实际源笔画和纸面尺度选择；过小会把粗字当背景，过大会跟不上急剧阴影。

s13 目标空白，只叠墨；s01 明确存在旧字，必须显式允许清除具体区域。若旧字与手指/阴影/纹理重叠、可见纸不足以恢复背景，不应自动清除或假装已完成。原保护区没有扩大或重观察。本次不改变保留区，也不意味着原保护坐标对所有新用途都正确。

两例最终目标外及 protected 像素变化均为 0；s01 恢复 mask 外的底图像素也为 0。最佳源归一+绘制 s01 0.64 秒、s13 两层 0.96 秒；s01 目标恢复并再次绘制另 0.67 秒（单次本机测量，不是性能基准）。原请求重放约 0.41/0.58 秒。

## 产品实现与固定输入回放

用户已要求先集中重放已保存失败，再开启新空项目；不继续用旧长案例逐个续跑找问题。下面的可选合同已在独立整合树实现，尚未用于新空项目真实制作；本次离线图没有登记成旧项目候选或采用结果。

在原平面合成里提供显式可选的“保留目标光照，仅迁移纸面内容”，而非默认所有 replace/multiply 开启。来源需是当前可见、真实纸面正文；输入声明可用空白源样本，程序依据实际尺寸估计局部背景。空白 target 可直接叠墨；非空 target 仅在调用者明确提供待替换旧内容区域时恢复这些平面像素，始终减去 protected_regions。旧 replace/multiply 行为不变。结果是新 DRAFT，仍需看真实输出；不用再付费猜全局白点。不要自动扩大 mask、重排文字或把摄影纸状态改成确定性字版。

现有 `image.composite_plane` 的 layer 增加可选 `paper_ink`，包含源图 `blank_sample`（现有 ImageRegion 的 x/y/width/height）和实际像素 `background_window_px`。它只配合 multiply 使用，不叠加旧 white_point。可选 `target_paper_cleanup` 指定 BASE 坐标的旧细印字 regions；识别出的旧字及 1px 抗锯齿邻域必须仍在显式区域、接收纸面墨迹的 target quad、不透明底图范围内，并减去保护区。没有样本或实际几何不可用时给具体错误，不猜坐标。仅实际调用新选项时加载已有 OpenCV 依赖。没有新增 Tool、Provider 或图片默认后处理。

Builder、Verifier 和实际 Tool Schema 同步该合同；同次观察可以给出空白区域，不要求再观察并猜一个白点。纸面处理是已明确用途的局部修复选择，不要求每张图都走该流程。

用上述两例实际源文件、原角对应和保护区，经产品渲染器再次回放：原请求仍与已落盘 v44/v43 逐像素相同。显式加入 paper_ink（s01 另加原研究的旧字区域）后，第 1 镜白片及重复签栏、第 13 镜灰内框均明显改善。源取样改用既有 ImageRegion 的 floor/ceil 覆盖；旧字扩边后再次裁到显式区域。两图 alpha 不变、目标外和保护区变化 0 像素，输入文件字节不变。本机各一次绘制耗时约 **0.64 / 0.71 秒**，不是端到端速度提升指标。根代理及独立复核者均看过产品输出的整图及纸面细节；原手写、红改和细线可见，s01 遮住的标题、远景小字与后续视频动态限制仍在。

14 个新增专项覆盖照明渐变与原黑/红笔画、alpha、显式清除边界、保护、实际尺寸和透明样本失败，以及真实 Tool 保存 → Service 重开 → QA 同源合同与未采用状态。包含旧合成、采样、白点、Tool、QA、时间码、交接、上下文、组合保存和 Skill 的联合回归 **224 passed / 41.94 秒**；14 个相关源文件 Pyright 0 errors。测试与固定输入结果不证明新 Agent 自主制作已通过。

持久工作区回放入口为 [replay_paper_composition.py](/Users/liyifan/Documents/ChatGPT/ai短剧调研/output/quality-e2e-2026-09-12/replay_paper_composition.py)，结果在 [product-replay-results.json](/Users/liyifan/Documents/ChatGPT/ai短剧调研/output/quality-e2e-2026-09-12/paper-replay/product-replay-results.json)。同目录保存原输入快照、完整产品请求、基准和新输出，实际原图仍取自旧运行库。脚本只调用纯渲染器，不连接 Service、DB 或 Provider。

## 公开依据与本地推断

- ImageMagick 官方 [Alpha Compositing](https://imagemagick.org/compose/) 说明除法混合及用模糊副本做照明校正。本实验采用除背景的思想；具体 median、噪声取样和两张图的效果是本地实验结论。
- Adobe 官方 [Blending mode descriptions](https://helpx.adobe.com/photoshop/desktop/repair-retouch/adjust-light-tone/blending-mode-descriptions.html)：Multiply 按通道相乘，白色中性，不能擦掉底图已有墨迹。因此 s01 与 s13 的前提不同。
- OpenCV 官方 [Smoothing Images](https://docs.opencv.org/4.x/d4/d13/tutorial_py_filtering.html) 和 [Morphological Transformations](https://docs.opencv.org/4.x/d9/d61/tutorial_py_morphological_ops.html) 提供所用局部估计算子；官方并未保证该组合自动分离所有纸面和墨迹。
- OpenCV 官方 [Image Inpainting](https://docs.opencv.org/4.x/df/d3d/tutorial_py_inpainting.html) 说明按显式 mask 用周围像素修复。这里仅在 s01 已知平整纸上做小范围旧印字恢复，不推断缺失手部或用隐藏生成模型。


## 可复核本地入口

本报告只提交说明，不复制输入摄影图到源码目录：

- [实际输入与原请求](/tmp/paper-normalization-study/actual-inputs.json)
- [初轮比较脚本](/tmp/paper-normalization-study/compare.py)、[最终离线脚本](/tmp/paper-normalization-study/refine.py)
- [初轮数据](/tmp/paper-normalization-study/results.json)、[最终数据](/tmp/paper-normalization-study/refined-results.json)
- [s01 三列对比](/tmp/paper-normalization-study/s01-refined-comparison.jpg)、[s13 两列对比](/tmp/paper-normalization-study/s13-refined-comparison.jpg)、[源墨迹细节](/tmp/paper-normalization-study/source-ink-details.jpg)
- [s01 全分辨率结果](/tmp/paper-normalization-study/s01-median-clean-target.png)、[s13 全分辨率结果](/tmp/paper-normalization-study/s13-median-ink.png)

从主项目根目录用本地环境运行 `backend/.venv/bin/python -B /tmp/paper-normalization-study/compare.py`，再运行 `backend/.venv/bin/python -B /tmp/paper-normalization-study/refine.py`。脚本仅读取本目录快照内的既有绝对媒体路径，不连接 Service/DB/Provider；调用主项目已有纯 renderer，把输出写到 `/tmp`。输入源图及脚本目录需保留，`/tmp` 不是产品持久媒体库。
