# 准确内容的四角透视合成

状态：第十轮调试修复正在本地实现和回归，尚未加载到实际制作进程。当前《一起签》仍不是新空项目验收，H3 为零。真实问题及暂停记录见 [03](03-real-production-record.md)。

## 实际缺陷与公开方法

第九轮新增的图示/遮罩已被产品 Agent 正常使用。s10、s12 的实际编辑请求明确要求“整页及全部内容一起转向”，也明确禁止只挪标题，但返回图仍将标题改为竖排、正文和表格基本正立。源图、图示、独立 mask 与局部合成接线正确，保护区像素未变；三张新候选的实际审图均正确拒绝。这里缺少的不是再堆一条相同提示，而是准确图文在平面上的几何操作能力。

[Kling Image O1](https://kling.ai/quickstart/klingai-image-o1-user-guide) 与 [Runway Advanced References](https://help.runwayml.com/hc/en-us/articles/41170686463635-Advanced-References-Use-Cases) 的涂画、标签和箭头是表达编辑意图的方法，并不保证每个字符、手势或透视都会遵守。本片已经验证这种方法能进入实际请求，也已经观察到其剩余质量限制。不能将官方示例当成本账户和当前模型一定能达到的结果。

[Adobe Photoshop Vanishing Point](https://helpx.adobe.com/photoshop/using/vanishing-point.html) 采用可见四角定义平面，再粘贴栅格内容并随平面透视缩放、旋转。它将准确内容与目标摄影平面分开。借鉴的是该操作方法；没有复制 Photoshop 或闭源产品实现。

[Pillow PerspectiveTransform](https://pillow.readthedocs.io/en/stable/reference/ImageTransform.html) 提供输出坐标到源坐标的透视采样，[OpenCV 官方几何变换教程](https://docs.opencv.org/4.x/da/d6e/tutorial_py_geometric_transformations.html) 说明四对不共线点定义透视映射。本项目已有 Pillow，本轮使用它和小型线性方程求解，不增加 OpenCV 安装或新的图像模型。

## 采用的工具合同

`image.composite_plane` 接收实际 `base_asset_version_id`、一到八个按顺序应用的 layer、可选前景保护区域和本次说明。每层明确精确 source、源平面四角、目标平面四角及 `replace` 或 `multiply`。源角省略只表示使用完整源图。

两组四角一一对应同一个物理角。将源页左上角映射到目标画面右上等位置，即可让标题、正文、表格、签名整体旋转；不能分别按屏幕左上重新排序而抹掉方向。四边形必须凸、无交叉、没有退化到像素线，点序保持同向，不隐式镜像。图像按照 EXIF 转正后的可见像素操作，不受图像 Provider 的 16 倍数输入尺寸限制。

适用范围是可看清的平面图形、屏幕、纸面或标牌，不涉及三维重建、移手、去字、修补旧纸或估计光照。源是摄影图时，源四角必须取纸面本身，避免把桌面和背景贴进新图。前景手指、笔等用实际观察的多边形保护；平面外及这些保护区保留底图像素。

两种合成的取舍必须按实际图像判断：

- `replace` 继承源像素与透明度，能覆盖旧内容，也会带入源图的光色与纸面质感；白底字版不自动变成自然摄影。
- `multiply` 可以保留空白目标表面的照明和纹理，但也保留原来已有的文字。它不适合清除错误文字或覆盖另一版表格。
- 新平面没有覆盖旧纸范围时，外露旧边和旧字仍在，必须先修底图或选适合的摄影几何。保护区域也不能靠猜测代替看见的遮挡。

渲染在共享锁外执行；最终短锁检查本次实际源文件及可用状态，再分配未占用的版本号。保存新的 DRAFT 候选及完整请求、精确 base parent 和来源关系，不改原图、current/canonical 或镜头绑定。并发保存的其他图和用户已选择媒体保留。结果回执包含实际新版本、尺寸和来源，并明确尚未作视觉采用判断；它不是辅助标注图，也不自动把 AssetPlan 标为完成。

QA 可在有充分实际图像依据时给出 `plane_composite_suggestion`，Builder 可直接接入本地工具；具体来源与建议校验见 [QA 交接记录](42-plane-qa-handoff.md)。缺少可观察四角、源图片或可界定遮挡时不给建议。新增工具不把平面合成变成每张图片的强制步骤。

## 离线对照与验证范围

在忽略目录 `output/quality-e2e-2026-09-12/observations/plane-composite/` 保存了底图、准确字版 replace、multiply 反例和摄影纸面 replace 对照，以及实际实验参数。它们使用现有真实图片做离线研究，没有登记到运行数据库或调用 Provider。实验人员选的四角和遮挡区没有作为产品 Agent 的生产输入。

对照表明整页内容可以按指定四角整体转向；multiply 确实留下双重旧字，摄影纸面比纯白字版更接近原场景，但仍有光差和残留旧边。这些试图不作为已修好的本片素材。独立工具回归使用带四个不同色块的源图证明旋转对应，并检查实际保护区及平面外像素，避免只断言工具返回成功。

实际 ToolRegistry → Service → 本地渲染 → Repository 的 7 项检查覆盖结果和来源保存、DRAFT 源、重启读取、current/canonical 与镜头不变、缺文件/拒绝/失效输入、慢渲染期间的新图片任务预留和用户媒体选择，以及源文件变更后的拒绝和新文件清理。与 11 项纯几何/像素专项合并执行，18 项通过（1.24 秒）；接线、渲染与测试文件 Pyright 为零。QA 建议及相邻检查 67 项通过，包含实际 QA 回执进入本地合成的离线调用，详见 [42](42-plane-qa-handoff.md)。最终联合 316 项通过（127.95 秒），覆盖 ProductionManager、工具上下文、镜头回执、图像编辑/合成与并发、QA 和 Skill；13 个本轮 Python 文件类型检查无错误。后续真实生成是否减少返工和提高照片质量尚待观察。
