# 单图 QA 的精确空间来源与实际检查职责

2026-09-12。本次只修已观察到的 s04 检查上下文接缝；未调用 Provider，未修改运行项目、图片或历史 QA。新输入接通不等于模型的空间判断已经通过真实复查。

## 实际观察

项目 `project-5b8286dc63e6` 的赵衡 s04 候选为 `asset-version-df5a3f9578c2`。唯一已完成的首批构图 QA 是 `work-order-16f6217ce9a1450f9848777736359ba8` t7，06:25:56 保存，模型调用 `model-d8edba7e1f37`。

Trace 目录：`output/quality-e2e-2026-09-12/runtime/outputs/short-drama-projects/project-5b8286dc63e6/studio-trace/studio-job-20f086971171`。

- 输入：`20260911T222410281267-69294832-model.input.json`。
- 输出：`20260911T222556549593-e858235b-model.output.json`。
- 实际使用量：`20260911T222556550687-20287d96-provider.language_generation.json`，Astra/xhigh、5 张图片、24,271 输入 / 4,925 输出 tokens（含 4,130 reasoning tokens）、106.242998 秒。视觉 tokens 已在 Provider 报告范围内，不能用文本字符数代替。

这次 QA **没有把纸向判成合格**：`recommended=false`、3.1 分，明确给出 `PROP_ORIENTATION_MISMATCH`（两纸同向而非对向）与 `CONTACT_STATE_UNCLEAR`（左拇指未触纸不够明确），结论是不应锁定为 0 秒初态。其余首批图当时没有完成自动 QA，不能把人工发现的开场手别错误写成该次 QA 的漏检。

背景则只得到“黑框隔断与右侧窗光延续场景参考”的描述，没有把当前西座反角与参考空景的观察方向分开。实际输入已经包含完整当前 shot04 rev1、实际保存的生成 prompt、明确初态的 review_focus、候选和四张正确的参考；不是整个 Shot 或生成意图丢失。

两个具体缺口：

1. 参考 `image_map` 只带身份、用途和权威信息，没有该版本自身的 descriptor。房间版本 `asset-version-d42fba8ad6bb` 已说明“南侧中央向北、北隔断、东窗、西墙北端门”，但这些方位文本没有进入 QA；当前 Bible 又没有人物/场景/道具实体。单凭图片看到类似装修不等于知道这个图的方位。
2. `inspect_asset_image` 实际编译的是旧 `ASSET_REVIEWER`，正文为旧工单的职责拆分；已有 `ASSET_VERIFIER` 关于可见空间关系、不能凭装修推断拓扑的专业方法没有接入。该调用也没有选择 method IDs，不能声称它已加载全部短剧方法。

## 公开依据与采纳范围

[Adobe 的 180 度规则说明](https://www.adobe.com/creativecloud/video/discover/what-is-the-180-degree-rule.html) 用人物连线与机位所在侧维持可理解的空间关系。本次借用其空间连续性判断原则：结合已声明的人物位置与机位检查背景方位，不把场景风格一致当作空间正确，也不机械禁止创作上有意的越轴。

[Runway 官方 Gen-4 References 文档](https://help.runwayml.com/hc/en-us/articles/40042718905875-Creating-with-Gen-4-Image-References) 区分场景参考与目标构图，并提供草图对构图、视线和物体位置的控制方式。这里采纳“参考的原构图与目标机位分开”的方法；没有接入 Runway 或宣称文本说明能保证空间生成正确。后述可选指示交接使用本产品已接入的本地编辑指示 Tool。

## 最小改动

- `asset_inspection.py` 的 `image_map` 参考项增加 `descriptor=reference.version.descriptor`。沿现有精确引用解析取值，不取 asset.current_version 的说明、不注入整份参考资产 brief，也不把旧场景资产的纸笔状态混入当前镜头。
- 单图检查改为编译既有 `ASSET_VERIFIER` 专业职责；没有根据题材自动选择方法 ID，没有新增 Agent、审批或必经检查轮次。
- 在专业职责中明确：先列可见门窗/台面/近远关系，再结合精确来源描述与当前 Shot 机位判断预期背景，分别报告可见矛盾和无法判定。不得猜造画外布局，不得把参考空景原机位直接搬作当前反角的正确背景。
- Tool 提示说明 descriptor 是精确参考版本的描述，既不是当前候选已满足要求的结论，也不要求候选复制参考图机位。候选实际生成请求、布局、当前 Shot、职责与可见/画外边界仍保留。

请求模型不变。新的来源描述仅进入下一次真实 QA 的模型输入；原始 Trace 不回写，既有不传 shot_id 的检查仍可调用。

## 同次观察交接可选编辑指示

主任务新增本地 `image.create_edit_guide` 后，QA 可选返回 `edit_guide_suggestion={regions,arrows,descriptor,source_asset_version_id}`。区域与箭头复用该 Tool 的现有模型；运行时固定 source 为 Image 1 当前候选，不接受模型换成其他参考。该对象可直接作为创建指示图 Tool 的 request，无需只收到文字结论的 Worker 再猜 mask 坐标。

只有实际看清、适合局部编辑时才建议；坐标使用完整候选经 EXIF 校正后的归一化坐标，不用局部放大图或参考图坐标。移动物体须覆盖原位置、明确目标位置及必要接触/阴影区域；大机位、多人站位和整体透视变化不能强行局限在小 mask。无法确定就留空，不自动生成新画面、不创建指示图、不发起编辑，也不修改推荐与问题结论。

`candidate_creation.local_edit_guide_available` 表示当前 Tool 能处理、且原尺寸可继续 masked EDIT 的本地有效候选。远程、拒绝、失效或 EXIF 尚未正向的源不提供不可执行建议；当前 renderer 对旋转源要求先生成正向局部派生，该限制不被 QA 绕过。尺寸直接复用 `image_models.parse_image_size`，与真实 guide renderer/EDIT 合同一致：包括 16 倍数、比例与像素总量等既有限制，不另定 QA 阈值。实际裁切图 900×1600、955×1696 不满足该合同，因此不返回误导性的原尺寸 mask 建议；仍可正常检查、裁切或选择其他现有编辑路径，不自动缩放已有图。

建议返回前再复用 renderer 的 `validate_edit_guide_geometry`，校验多边形在实际原像素上仍有面积、箭头不会归为同一像素。归一化值合法却在像素上退化时，只丢弃可选建议，原推荐与问题结论保留。归一化几何本身共线/重合等也只令 `edit_guide_suggestion` 为空：这一可选字段先独立验证，不让它的共享模型 validator 抹掉已得到的有效检查；其他 QA 字段和完整 JSON 仍按原合同验证。

PM 只在原 `asset_review` 实体白名单中增加该可选小字段。检查后执行其他 Tool 或重启，仍按精确候选版本保留几何与来源；没有新增历史缓存。旧 QA 输出缺少字段时仍可解析为 null，不补造旧建议。此扩展新增 ToolRegistry 内部 typed output 的可选字段；HTTP Tool 通道沿用 generic output，没有 `SemanticAssetReview` 专属 REST response。主任务已重生并检查 OpenAPI/TS，产物与 Git 相同属预期；不宣称公共 REST 专属响应 schema 已变化。

## 验证与待验证边界

新 fixture `tests/fixtures/asset_inspection_spatial_context.json` 保留本次真实 Shot、生成 prompt、focus、有序引用及精确参考版本说明。图片使用本地色块验证实际编码和发送顺序，明确不是以色块检查视觉正确性。

新测试从真实 Registry → Service → 图片读取/编码 → QA 输入捕获，覆盖自动派生参考与显式参考两种调用；确认完整当前 Shot、生成意图、5 图顺序与每份原参考职责不变。即使房间 current_version 已指向别的版本，输入仍用指定版本的 descriptor；无关当前 brief 和未选新版本说明不会进入。调用后项目与采用状态不变。

空间来源切面的新旧五套测试共 **56 passed / 4.33s**：`test_asset_inspection_spatial_context.py`、`test_asset_inspection.py`、`test_asset_image_regions.py`、`test_asset_crop_suggestion.py`、`test_text_plate_states.py`。覆盖既有身份/道具检查、历史来源、跨项目引用、图片局部、裁切建议及字版状态。该批 Pyright **0 errors / 0 warnings**；Epicurus 独立只读复核未发现问题。

后续编辑指示专测从实际 QA Tool 返回 → 保存 SpecialistTurn → 其他 READ → 重开 Store → Worker 模型输入，验证几何逐值保留、来源覆盖为实际候选、建议为空兼容，以及远程/拒绝/失效/旋转源只丢可选建议而不改变问题结论。另覆盖两种本片实际裁切尺寸、低于像素下限的尺寸、归一化有面积但栅格成线/箭头同像素，以及归一化几何本身共线/重合。评分越界与非法 JSON 仍拒绝，未借可选建议放松基础检查合同。

最终冻结联合 **82 passed / 7.57s**：前述五套加 `test_asset_edit_guide_suggestion.py`、`test_asset_worker_context_efficiency.py`。QA、PM 和三份相关测试共五文件 Pyright **0 errors / 0 warnings**；`git diff --check` 无错误。此次测试使用本地 Fake 语言响应，验证输入、可执行合同与跨 Tool/重启交接，不声称真实视觉模型已输出正确编辑范围。

建议由主任务在受控加载后，判断是否对**同一张 s04 图**做一次真实 QA：保留原纸向/接触判断，检查是否识别或准确说明西座反角与北隔断/东窗背景关系。单次成功仍只证明这个反例改善，不是空项目端到端稳定性验收。
