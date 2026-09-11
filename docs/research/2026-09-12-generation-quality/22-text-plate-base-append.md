# 字版基图追加：保留已完成排版，只绘制新增内容

2026-09-12。当前属于同一项目的前置链路调试改进；本实现没有调用 Provider、修改运行项目或操作任务，不作为新空项目完整制作验收。

## 实际返工与根因

项目 `project-5b8286dc63e6` 的情况说明以 v5 单签字版为来源，v7、v8 均明确传了 `parent_version_id=asset-version-9d931209f7ec`。当时 `parent_version_id` 仅保存来源关系，`compose_text_plate` 每次新建空画布；Worker 必须重输整页文字块、字号、坐标与横线。

| 比较 | 真实请求与 PNG 变化 |
| --- | --- |
| v5 → v7（第 34 轮，04:29:54） | 原有 5 个文字块均有坐标、字号或对齐变化；正文左置，横线变长且下移。1440×2032 原图的实际像素差异外接框为 `(174,264)–(1239,1657)`。04:30:50 产品 QA 返回 3.5 分及 `PROP_LAYOUT_DRIFT`。 |
| v5 → v8（第 37 轮，04:31:25） | Worker 第 36 轮回读 v5 的精确 Tool 参数，恢复原 5 个文字块和全部横线，仅新增经办“许岚”。差异范围缩至 `(536,1219)–(671,1284)`。04:31:50 产品 QA 返回 5.0 分。 |

两次差异由 SQLite 只读取得的实际请求、原 PNG 和 Pillow 像素比较确认。v7 的检查结论合理，然而“回读参数 → 整页重填 → 重新检查”的返工可通过局部追加能力减少；不能把这些本地字版算作图片 Provider 生成或真实手写摄影。

## 借鉴的公开机制

- [Adobe Photoshop：Duplicate layers](https://helpx.adobe.com/photoshop/desktop/create-manage-layers/get-started-layers/duplicate-layers.html)（官方页面标注 2026-02-23 更新）提供复制既有图层或图层组的机制。这里采用“保留已完成内容，只添加本次变化”的工作方法，没有接入 Photoshop 或宣称 MiniMax 私有运行时使用同样实现。
- [Pillow：ImageDraw](https://pillow.readthedocs.io/en/stable/reference/ImageDraw.html) 官方文档支持向现有图像添加图形/文字，并展示透明文字层与底图合成。本项目已经依赖 Pillow，直接复制实际 PNG，在独立透明层绘制新文字，再进行 alpha 合成，无需新增依赖或调用生成模型。

## 已实现合同

现有 `image.compose_text_plate` 支持两种明确用法：

1. 原全量绘制请求保持兼容，继续使用 `title/exact_lines` 或 `text_blocks/lines`。
2. 已有字版仅需增加内容时，提供 `base_version_id`、非空 `append_text_blocks`、完整 `content_state`、说明。无需重新输入尺寸、旧文字和横线。追加模式继承基图实际尺寸及既有样式；序列化时附带的全量模式尺寸/颜色默认值不会覆盖基图。非空 `title/exact_lines/text_blocks/lines` 与追加模式互斥，避免把完整重排误当追加。

示例省略真实运行 ID：

```json
{
  "base_version_id": "已有单签字版的精确版本 ID",
  "append_text_blocks": [{
    "text": "许岚",
    "content_box": [0.37, 0.592, 0.3, 0.048],
    "font_size": 70,
    "vertical_align": "center"
  }],
  "content_state": {
    "name": "经办许岚、复核赵衡均已签",
    "required_visible_text": ["情况说明", "本次报价出现错误，现补充核对。", "经办", "复核", "赵衡", "许岚"]
  },
  "layout_description": "保留单签原版面，只在经办空白处追加许岚。",
  "descriptor": "双签完成的准确文字位置底稿。"
}
```

程序读取当前项目明确选用的真实 TextPlate PNG，允许可用 DRAFT；未知、缺失、空文件、不可解码、非 PNG、非 TextPlate、已拒绝或失效来源会报具体 `base_version_id`。实际解码尺寸必须与保存的字版及版本尺寸一致。绘制在锁外进行，提交前重新检查来源 metadata 及文件路径、大小、mtime，防止读取期间换源；没有加入文件哈希或额外审批。

旧 title/exact_lines、text_blocks、横线及原始字体说明均保留。实际新增块累计保存到 `AssetTextPlate.append_text_blocks`，每块记录本次使用的字体，后续再次追加与 QA 均可读取完整文案。旧字体不因追加被重新渲染。新增块越界或文字放不下时，错误定位 `/request/append_text_blocks/{index}`，不截字、不保存半成品。

固定 brief 文案与完整状态校验发生在“基图已有文字＋累计新增文字”上；不是只校验本次姓名。漏固定文案、改字或未声明姓名仍拒绝。追加结果是新 DRAFT，源候选、目标 current、approved/canonical 指针、共享 brief 均不变；明确目标 Asset 和状态仍由调用者选择。`parent_version_id` 保留旧谱系语义，真正复制的基图另存为 `derivation_references` 和本地来源记录，并返回准确 ArtifactRef。

AssetPlan 的 Intent/Spec 透传追加字段，保存、重新加载、执行前校验与完成登记均使用同一内容解析；完成候选必须包含所声明基图来源及匹配的状态/完整文字。`artifact.get_asset_work_context` 只把当前所选工作中的基图加入媒体集合，提供实际路径/尺寸，不回灌无关版本。Builder/Planner 方法提示优先复用旧字版，删除或重排仍使用原全量模式。

最终适配器复核发现一处独立接缝：真实图像 HTTP 请求曾自行从原 `text_blocks` 拼 `rendered_visible_text`，遗漏累计追加文字；虽然 `content_state` 已包含新增姓名，两份清单会互相矛盾。现已统一调用 `AssetTextPlate.required_visible_text()`。对原全量版和真实基图追加版分别执行本地 Fake 图像任务，再经过真正 HTTP adapter 的内存 multipart transport，核对上传 PNG 对应的 prompt 同时包含完整状态与实际渲染文字，最后核对摄影候选 QA 读取选中参考的完整状态。这项不以 metadata 单测替代实际 adapter 验证。

## 本地验证

在 `backend` 中运行：

```sh
.venv/bin/python -m pytest tests/test_text_plate_append.py tests/test_text_plate_states.py tests/test_asset_plan_runtime.py -q
.venv/bin/python -m pytest tests/test_text_plate_tool.py tests/test_text_plate_layout.py tests/test_text_plate_states.py -q
.venv/bin/pyright short_drama/asset_workbench.py short_drama/image_models.py short_drama/models.py short_drama/production_contracts.py short_drama/asset_plan_reconciler.py short_drama/service.py short_drama/tools/image/__init__.py tests/test_text_plate_append.py
```

- 新追加专项 18 项通过；新追加＋状态＋完整 AssetPlan 联合回归 79 项通过（16.81 秒）。原 Tool／布局／状态 35 项通过（5.02 秒）。这些集合有重合，不相加宣称独立用例数。
- 相关源文件及新测试 Pyright 0 errors、0 warnings；差异空白检查通过。
- 使用真实纸张坐标渲染空白→单签→双签，逐像素验证原有内容不变；核对保存/重载后的旧候选、current、brief 与完整状态。覆盖透明 PNG、旧流式布局、真实来源异常、精确文字错误、溢出、计划重载/完成登记与基图工作集。
- 已实际查看本地测试输出 [双签字版](/tmp/text-plate-append-double.png)：标题及正文居中，两个栏位与横线保留，上许岚、下赵衡，字形无裁切。此 PNG 是本地测试样例，不是运行项目的新候选。
- 最终 adapter 补丁与操作映射联合回归：`tests/test_image_operation_mapping.py tests/test_short_drama_assets.py tests/test_text_plate_states.py tests/test_text_plate_append.py tests/test_openai_image_client.py` 共 61 项通过（7.43 秒）；相关 image 模型、HTTP adapter、Tool 和新增／更新测试 Pyright 0 errors、0 warnings。
- Arendt 独立只读复核追加核心、保存检查、精确基图工作集及计划完成登记；随后单独复核最终 HTTP adapter 修正与 full/append 参数化回归，确认实际 multipart prompt 同时包含完整状态和渲染文字，未发现具体阻断。

## 范围与限制

这里只追加已有准确图版上的新文字，不擦除、移动或重排旧内容，不进行 OCR、自动猜测签名状态、手写风格生成、摄影透视、遮挡或视频跟踪。摄影派生仍按实际任务需要使用已有图像 Provider；字体姓名底稿不证明真实书写动作。测试不等同于产品已在当前真实项目采用新模式；新空项目完整验收仍待后续执行。
