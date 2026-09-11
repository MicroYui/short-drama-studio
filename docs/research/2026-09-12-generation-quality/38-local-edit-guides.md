# 局部编辑图示与遮罩

## 真实问题与方法来源

《一起签》调试轮的首批四张镜头构图图均把本应朝画面左/右的纸张标题边画在上方，尽管请求已说明世界方向和图像方向。s04 的真实视觉检查也指出两张纸朝向与手指接触问题，尚不能登记为可采用素材。图片请求成功不代表这些内容要求已完成。背景机位检查缺少上下文另见 [37](37-asset-inspection-spatial-context.md)，构图复用与参考数量分析见 [35](35-composition-reference-review.md)。

本轮参考公开产品方法，不推断闭源内部实现：

- [Kling Image O1 用户指南](https://kling.ai/quickstart/klingai-image-o1-user-guide)：以涂画标记区域、位置及编辑意图，结合文字进行图像修改。
- [Seedream 5.0 Pro](https://seed.bytedance.com/en/seedream5_0_pro) 及其[设计能力介绍](https://seed.bytedance.com/en/blog/beyond-generation-it-understands-design-introducing-seedream-5-0-pro)：空间标注、草图与分层设计可以帮助表达目标布局。
- [Runway Advanced References](https://help.runwayml.com/hc/en-us/articles/41170686463635-Advanced-References-Use-Cases)：箭头、标签和形状可说明元素位置、构图及视线方向。
- [Runway GPT Image 2](https://help.runwayml.com/hc/en-us/articles/51111417337363-Creating-images-with-GPT-Image-2)：同一模型类别支持参考图与草图，因此本轮先补充可见编辑输入，不以替换现有图片 Provider 代替方法修复。

由此采用：已经有实际可用构图时，用观察到的局部范围和可见目标位置辅助修改。涉及整个人物站位、机位、背景关系时，仍由 Agent 判断是否需要整体编辑或重新构图；不把局部遮罩变成每张图的强制步骤。

## 当前实现

新增 `image.create_edit_guide`，接收精确本地图片版本、归一化多边形区域、可选箭头和说明，使用 Pillow 生成同尺寸的 PNG 图示和独立 RGBA 遮罩。遮罩透明区允许修改，不透明区保护原图。原始文件、当前采用版本、母版、镜头绑定与参考图集保持原值；两份辅助版本记录真实父图和参数，不代表修图完成。

后续复用已有 `image.enqueue` 的 `EDIT` 路径：实际原图作为 parent 和第一张 primary，图示作为额外 `LAYOUT_GUIDE`，遮罩通过 `mask_version_id` 单独传入，输出尺寸与原图相同。推荐PNG输出和 `mask_feather_pixels=0`，让选区内直接使用新像素并保留选区外原像素。当前合成器在非零羽化时还会将柔化alpha乘以原始editable遮罩，因此原保护区仍为0，混合发生在选区内侧边缘，不扩到圈外；若要彻底替换错误纸边和原文字，不应默认依靠羽化来掩盖布局错误。JPEG/WebP会重编码，不能宣称逐像素不变。红框、箭头和标签必须在请求中声明为说明性标注。

QA 可选输出 `edit_guide_suggestion`，包含实际看见的区域及目标箭头，运行时将其绑定到本次检查的真实候选 ID，并在 Worker 后续读工具与重启后保留。看不清坐标、缺少本地可用文件或需要整体构图变化时可不提供建议；Worker 不靠文字描述猜测坐标。具体见 [37](37-asset-inspection-spatial-context.md)。

入口复用现有图片请求的尺寸校验，避免为 900×1600 等无法直接用于当前 masked EDIT 的裁剪结果保存一组不能执行的图示。源文件含 EXIF 旋转时，先通过现有本地裁剪取得转正像素。多边形与箭头在实际像素尺寸再次检查，拒绝归一化面积非零、但落到像素后退化为线或同一点的输入。无效输入不保存辅助版本。

## 验证边界

真实 s10 图片只用于离线渲染示例，图示和遮罩保存在忽略目录 `output/quality-e2e-2026-09-12/edit-guide-review/`。未写入运行项目、未提交 Provider，示例坐标不是对本次生产内容的人工修改。

回归覆盖实际 ToolRegistry → 本地双文件及回执 → 既有 EDIT/Fake Provider → 原始保护区像素保留，并检查 current/canonical、原文件及镜头引用不变。无文件、已拒绝、无面积区域、EXIF 旋转、不支持尺寸及像素退化输入覆盖入口失败后的保存行为。新增 8 项通过（0.88 秒），相关 5 个 Python 文件 Pyright 为零；新增紧邻遮罩边界的像素断言后，与已有 EDIT、真实 adapter、Skill 加载及测试规范联合 80 项通过（8.69 秒）。后续并发版本规则的联合覆盖见 [36](36-concurrent-image-version.md)，这些集合有重复节点，不相加为独立测试总数。

### 独立复核与本地像素接线

独立审查确认图示与遮罩分别返回真实AssetVersion及数字版本号，源版本和两份新版本之间有明确派生关系；保存辅助文件不调用 `_append_version`，因此不会把图示或遮罩选为current或canonical，也不改原图、镜头绑定和ReferencePack。箭头、标签与薄红色覆盖可读，透明遮罩使用alpha=0表示可编辑，alpha=255表示保留，与既有EDIT上传及最终合成约定一致。

审查另复现三个具体边界并分别交由对应实现修复：

- 归一化三角形 `(.25,.25),(.75,.25),(.5,.250001)` 在1440×2560图像上落成一像素横线；不能只以不同像素点数量判断其仍有面积。入口已增加实际像素多边形面积及箭头起终点校验。
- 本片既有900×1600、955×1696人物裁切尺寸不符合现有图片请求的16倍数合同。图示生成与QA建议使用相同尺寸规则，不能先成功返回一份无法原尺寸进入masked EDIT的建议，再让下一次Tool失败。
- 同一图片版本同时保存local_path与external_url时，旧 `_resolve_provider_inputs` 总是优先远程URL，可能使Provider看到另一份图片，而遮罩和本地合成仍对应本地像素。现仅对masked EDIT改为发送已验证的本地bytes；原图、附加图示及遮罩均适用，本地丢失时具体报错，不退回远端。没有mask的原外部优先行为保持兼容。

最后一项以独立tmp项目及Fake HTTP复现：新5项在修复前4失败/1通过，修复后与既有12项图片adapter回归共17通过（1.18秒）。测试经过真实adapter的multipart装配，逐张解码上传的primary、guide、mask，确认颜色/透明度来自本地原文件、顺序准确且没有远程GET；分别删除三类本地输入时都指出实际版本ID。`asset_workbench.py`与新 `test_masked_image_local_source.py` 的Pyright为0，限定diff检查通过。没有访问真实Provider或修改运行DB。

实际离线示例文件为 `edit-guide-review/s10-edit-guide.png`、`s10-edit-mask.png` 及仅用于显示透明区的 `s10-edit-mask-alpha-preview.png`（相对本轮output目录）。真实源为s10构图 `asset-version-a8b4587fe910`；两份产物均1440×2560，原文件bytes前后相同。遮罩有668,484个透明编辑像素、3,017,916个不透明保留像素。示例框包含部分左手，只演示可见标注与真实透明区域，不是对本片认可的生产选区或已完成修图；请求另保存在 `s10-request.json`，未登记为项目素材。

这些检查只证明工具合同和局部合成工作正常。实际 Provider 是否按图示改对纸张、文字和手部仍须恢复调试后观察；当前调试项目不是新的空项目端到端验收，H3 仍未开始。
