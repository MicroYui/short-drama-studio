# 图片资产与下游连续性：实际检查、改动与验收

日期：2026-09-12。范围是图片制作与图像参考，不把候选落盘、DRAFT/APPROVED 或接口成功当作画面质量。与[本轮总报告](01-quality-audit-and-adoption.md)配合阅读。

## 当前结论

现有图片链并非只传文字：它已经向真实 GPT Image 2 提交有序参考图片，并声明身份、服装、空间、道具、构图等职责；参考生成和编辑使用高输入保真。眼下更直接的质量风险是**参考图片里没有足够清楚的所需信息，或图片承担的职责与后续镜头不匹配**。增加“保持一致”的句子不能补回小脸的原始细节，也不能把空场大景变成演员交接初态。

已有实现值得保留：`providers/openai_image_client.py:115` 校验实际图片上传顺序，`:137` 把 reference role/authority/purpose/alias 写入实际请求，`:164` 发送 size/quality/background/output_format，`:184` 对 REFERENCE_GENERATE/EDIT 设置 `input_fidelity=high`。`asset_workbench.py` 已有导入、派生系谱、局部 mask、确定性拼图/文字板以及失败请求的恢复边界。本轮没有为了换模型而重写这些有效机制。

## 看过的实际图片

以下为旧案例的观察记录，不是本轮新空项目生成结果。来源索引为 `output/mature-product-acceptance/h3-compile/source-project.json`，该文件有完整 AssetVersion 和来源记录。表中前四项的原文件已于 2026-09-14 清理，仅保留文件名和此前的观察结论。

| 实际文件 | 可见事实 | 对当前用途的影响 |
| --- | --- | --- |
| `v3-asset-version-7f0663cb928f.png`（已清理） | 2560×1440，主角蓝白校服、黑包、挂绳的四个全身视图；服装与体态信息清楚，单个脸部占比较小 | 适合服装/全身参考，不能直接证明它也足以固定近景面部。应查看脸部源像素；能裁出有效脸部才裁，细节不足时从同一身份来源补近景 |
| `v8-asset-version-acc395643eeb.png`（已清理） | 1672×941 的食堂四机位图，能看见餐桌、窗口、设备、门窗等环境 | 提供环境外观与部分空间依据，但没有真实演员、餐盘和窗口接触关系；不能当作交接初态已经成立 |
| `v2-asset-version-cf1f67477963.png`（已清理） | 1024×1024，三格空餐盘 | 能约束物体形制；不能证明本镜开场餐盘已有米饭和菜 |
| `v2-asset-version-c8c10f31ae76.png`（已清理） | 1024×1024，浅椭圆勺头和直柄 | 有明确可比较的部件形制；后续生成变成圆深勺不能归为“同样是一把勺子”而忽略 |
| `output/mature-product-acceptance/h3-compile/selected-reference-contact-sheet.jpg` | 本次旧 H3 请求的 8 张实际参考统览 | 主角小脸与其他较清楚人物参考的信息量不均衡；这是输入风险，不是已证明发生身份互换 |

已有 H3 的 `video-inspection.json` / `inspection-summary.json` 保存了 18 帧观察，报告了餐盘/勺子差异、视线轴和动作连续性问题。该检查对主角身份明确存在参考辨识限制，因此不能说它已可靠证明主角换脸。静态观察也不构成听审。上述问题支持“候选尚不能采用”，不能单独归因于图片模型。

## 成熟实现中能直接迁移的方法

| 公开来源 | 已确认做法 | 本产品采用的边界 |
| --- | --- | --- |
| [Kling Element Library，2026-02-05](https://kling.ai/quickstart/klingai-element-library-3-user-guide) | 多图元素由主图与补充视图组成，支持在后续图像/视频中复用 | 已有 ReferencePack 可以承担这种用途；主图和补视图要有真实信息，不能只建对象关系。Kling 的模型级主体绑定不等于 H3 的普通文字提示 |
| [Kling IMAGE 3.0 Omni，2026-02-06](https://kling.ai/quickstart/klingai-image-3-omni-user-guide) | 系列图可依据共同参考生成相关画面；示例包括反打、景别/机位变化和保持其他内容的局部修改 | 派生视图共享身份与空间依据；用于观看的整卡与提供给模型的有效单图分开。官网演示未与本项目进行同条件质量比较 |
| [Seedream 5.0 Pro](https://seed.bytedance.com/en/seedream5_0_pro) | 官网展示区域标注、草图引导编辑、图层拆分与摄影效果 | 修改应明确作用区域和保留内容；已有像素的裁切由本地确定性工具完成。本轮不据官网展示直接替换图片 Provider |
| 已保存 MiniMax Design 3.0.11 `workflows/drama-series/reference/assets-blocking.md:8`、`:168`、`:179` | 脸部可辨识、同脸状态从共同源图派生、道具形制与生命周期状态共同维护 | 本地实际角色指令保留这些目的；不强制每个对象生成同一张 16:9 卡、不搬入固定审批阶段。3.0.12 不同公开 Skill 的版式也不同，详见总报告 |
| [MiniMax 图片 API 指南](https://platform.minimax.io/docs/guides/image-generation) | 公开文档描述 image-01 文生图与人物参考生成 | 这是独立公开 API，不能据此断言 Design 产品内部使用它；本轮未新增此 Provider |

这里借鉴的是可执行方法和媒体使用方式；没有读取或复制闭源 Agent 引擎、私有通信实现或安装包前端。

## 按质量收益排序的实际改造

1. **先使参考可辨识且用途正确。** 近景需要面部依据；服装、体态、场景与构图使用对应参考。角色 Planner/Builder/Verifier 正文已补入具体判断，避免把全身整卡当作所有镜头的万能输入。源图没有足够脸部像素时，裁切不会修复它，必须用同一身份来源派生清晰图。
2. **复杂交互需要真实关系画面。** 空场图、人物卡和道具母版分别正确，不证明它们在本镜的接触面、视线和当前状态正确。Builder 指令要求按需形成实际演员/空间/道具的关系初态；H3 软构图输入和 `video.extract_frame` 由本轮另一切面实现。只在当前镜头需要时做，不变成每镜强制付费步骤。
3. **审核当前输出，而非要求每张图完成整个资产。** `asset_inspection.py:642` 和 `service.py:11252` 现在读取候选对应 ImageTask 的真实 prompt、操作和 output_layout；本地裁图/拼图读取其真实来源。`qa.inspect_asset_version` 可带 `review_focus`，明确本次疑问。总体 brief 要四视图，单张脸部输出可以正确满足当前任务。
4. **让局部问题能直接操作和检查。** `image.crop` 注册精确局部为可引用 DRAFT，源图、原 canonical、镜头引用不变；`AssetVersion.image_crop` 保存源版本、EXIF 纠正后的真实尺寸和像素矩形。使用归一化可见坐标，不缩放、不付费。QA 的 `regions` 可在同一次视觉调用追加候选或实际参考的细节，保留全图、实际裁区和来源，避免仅看缩小整图；透明图片检查使用中灰底，防止白色文字在白底消失。
5. **把真正采用的图片传到生成输入。** 沿用现有有序 `reference_inputs`。新增端到端 Fake 检查确认一个 DRAFT 裁图作为身份来源时，Provider 实际收到该裁图的 200×200 像素及精确版本，而非父整卡；结果派生记录指向实际输入。采用关系继续用既有 ReferencePack/AssetPlan，候选成功不自动表示所有资产完成。

源码入口：`image_models.py:114`、`models.py:1475`、`image_regions.py`、`asset_workbench.py:407`、`tools/image/__init__.py:173`、`tools/qa/__init__.py:242`。专业方法进入实际 `skills/shared/roles/asset_planner.md`、`asset_builder.md`、`asset_verifier.md` 与 `inspect_asset_image` 的当前视觉请求指令，未修改上游原文冒充本地原创。

## 新空项目的全资产真实验收

父任务正在 `output/quality-e2e-2026-09-12/runtime` 中运行一次从空项目开始的产品制作；本文件不预填它的成功结果。执行均经 StudioJob/Runner/Tool，不再由独立脚本生成媒体后导入并冒充产品完成。

- 以实际剧本和镜头所需对象为资产清单，Agent 决定必要数量；每份最终采用图记录真实 ImageTask、输入版本、输出文件、角色用途及花费/等待时间（供应商未提供的数值保持未知）。
- 查看原图和必要局部。人物比脸型、发际、五官关系和服装，场景比可见门窗、台面、近远关系，道具比部件形制及本镜状态；脸部/遮挡不足明确写缺证据，不用总分掩盖。
- 对复杂双人动作，核对用于该段的构图/初态是否真的出现所需演员、站位、接触和道具状态；有可用单图就复用，不补无关卡片。
- 核对最终 H3 实际上传的版本和用途与已采用媒体一致，再观察真实视频中的同一人物、道具、轴线和动作；声音走实际音频检查。图片自查通过不替代成片观看。
- 保留总耗时、图片/视频调用数、内容返工、开发修复、人工接管和最终采用结果。一次案例能证明这次闭环是否成立，不能推算普遍成功率；不为刷指标重复付费运行。

## 配置与本轮检查边界

新 runtime 应从当前应用数据目录读取 `short-drama-language-provider-settings.json`、`short-drama-image-provider-settings.json`、`short-drama-provider-settings.json` 三份配置。当时核对的图片模型为 `gpt-image-2`，Azure host 为 `datasetannotator-openai.openai.azure.com`，已配置图像 generation/edit 路线；语言配置使用 4141 代理和 gpt-6-astra/xhigh，视频使用 H3。旧运行配置已于 2026-09-14 清理，重新运行时以实际使用的配置为准；不在文档输出 key。

`image.capability.can_generate` 只代表配置中有 key，不证明账户当前可调用或额度可用。需要以本次产品任务真实请求/回执确认；透明背景与供应商能力限制仍由适配层处理，不承诺所有输出参数在 Azure 均可用。本子任务没有发起付费图片/视频请求。

本地检查：`tests/test_asset_image_regions.py`、`test_asset_inspection.py`、`test_asset_reference_sheets.py`、`test_image_capability_tool.py`、`test_openai_image_client.py` 共 **62 passed**。覆盖实际像素/alpha、EXIF 坐标、无效来源不写入、局部 QA 与当前单图任务意图、裁图真实进入 Provider 输入、既有图片适配契约；相关 Pyright 为 **0 errors / 0 warnings**，`git diff --check` 通过。它们证明这次工具实现和输入交接，不证明真人生成质量已提高；后者由本次新影片的真实产物判定。
