# 准确文字图版：真实失败、参数修复与本地排版检查

2026-09-12。此次检查以只读 SQLite（`mode=ro`、`query_only=ON`）读取 debug 项目 `project-5b8286dc63e6` 的现有记录；修复只改代码、Skill 与独立测试 fixture。没有调用图片/视频 Provider，没有修改 debug 项目或执行其待处理操作。本次续跑仍属于 debug，不能代替用户要求的新空项目完整制作验收。

## 真实失败与后续隐患

`studio-job-20f086971171` 中，纸笔 Worker `work-order-12f15f76ccb84cf1b6346a8ab5374c03` 在北京时间 02:27:45 调用 `image.compose_text_plate`。`studio_events` 的 1531/1532 分别保存开始与失败：`INVALID_ARGUMENTS`，路径只有 `/request`，原因是 `Text plate exact_lines must be trimmed and non-empty`。

该请求并未遗漏正文。它使用 1536×2176 画布，标题为“情况说明”，正文为“本次报价出现错误，现补充核对。”，后面是“经办：________________”“复核：________________”。中间的空字符串意在保留写字空间。旧 schema 没有明确表达空行禁用，整个模型的校验又使错误无法指出具体元素。

继续检查发现两个实际问题：

1. Worker 随后提交的待处理请求删除空行，改为 1440×2032。即使解决空行校验，旧文字比对仍会把标签后的冒号/下划线判成与 `required_visible_text=["情况说明","本次报价出现错误，现补充核对。","经办","复核"]` 不一致。要求 Agent 改资产文案来适应签字装饰不合理。
2. `layout_description` 只保存意图，旧 Tool 只能整体排流式文字，无法执行“标题居中、正文居上、两个签字区分置下半部”的准确位置。删除空行的请求确实会把所有文字挤在上部，API 成功不代表达到用途。

物理资产语义也受到 Tool 影响。此前 turn 1 读到纸张 Asset 的 `brief=null`，不能声称发生过已存 `PHYSICAL_MASTER → SCREEN_CONTENT_PLATE` 的枚举变更。turn 12 的 `artifact.update_brief` 新建了 `SCREEN_CONTENT_PLATE` brief，理由明确写了“先在同一道具对象内保存准确字版，再派生摄影纸张参考”“完成字版后恢复物理道具呈现模式”。这表明旧 Tool 仅接受屏幕内容的前置要求，诱导 Agent 暂时改变纸张主体的职责。修复不会直接改写这份既有运行数据。

## 公开方法与工程取舍

[Pillow ImageDraw 官方文档](https://pillow.readthedocs.io/en/stable/reference/ImageDraw.html)提供实际字体测量、文字定位与线段绘制，超出图像的绘制部分会被裁掉。因此本地渲染应先测量字形边界，再验证所给范围，不能默默截字。[W3C SVG 文字规范](https://www.w3.org/TR/SVG2/text.html)与[线段规范](https://www.w3.org/TR/SVG2/shapes.html#LineElement)分别表达字符内容和几何位置，这一职责划分适合准确纸面、屏幕与标牌内容。

这里采用上述公开且可本地验证的原则，继续使用已有 Pillow；没有新增渲染依赖，也没有推断即梦、Kling 或 MiniMax Design 的私有排版实现。没有建立独立版式编辑器或固定文件模板。

## 已实现合同

- `AssetTextPlateComposeRequest.exact_lines` 保留旧请求，空字符串代表一个空白排版行；带内嵌换行的非法行返回 `/request/exact_lines/{index}`。空行不算可见文案。
- 新增 `text_blocks`，与 `title/exact_lines` 二选一。每块提供一行准确文字、归一化 `content_box=[x,y,width,height]`、输出像素 `font_size`、水平/垂直对齐及可选颜色；无隐含边距，数组按文案阅读顺序填写。新增 `lines` 保存归一化起止点、像素宽度与颜色，可配合两种文字方式画签字线、分隔线或划线。
- 块的字形按真实字体测量，溢出返回准确块索引、所需/可用像素、当前字号和修复方法；不静默缩字或裁字，不保存半成品版本。线条越出画布同样返回准确索引。
- 文案只容忍行首尾排版空白、行末标签冒号与尾随连续 `_ / ＿ / ─ / ━` 装饰。正文内部的撇号、斜线、括号、下划线、冒号、金额、小数点、正负号与百分号保留比较；缺字、改字、增添姓名/关键文字仍报具体 expected/received，不为了排版修改正确 brief。
- 已有 `PROP + PHYSICAL_MASTER` 可直接生成同一资产下的 `text_plate.purpose=PHYSICAL_SURFACE_CONTENT` DRAFT 候选；保留物理 brief、current/canonical 和既有版本，只追加准确表面内容版本。明确版本可继续作参考来源，平面内容不会自动替换摄影母图。原 `SCREEN_CONTENT_PLATE` 行为与历史 metadata 兼容。
- 视觉检查输入携带实际 `candidate_creation.text_plate`，按候选职责检查文字/版式，不要求平面内容已经呈现纸厚、透视或手部接触，也不把字版成功等同于物理母图完成。Builder/Planner Skill 已说明上述边界及实际参数，避免靠描述期待不可执行的版式。

实现位置：`backend/short_drama/image_models.py` 的请求合同；`models.py` 的 `TextPlateTextBlock / TextPlateLine / AssetTextPlate`；`asset_workbench.py` 的 `compose_text_plate`、文案验证与本地绘制；`asset_inspection.py` 的候选检查范围；现有 `tools/image` 和资产 Builder/Planner 角色正文。未新增 Tool 名称，父任务统一重新生成 OpenAPI。

## 真实 PNG 检查

使用独立临时 SQLite fixture，通过真实 `image.compose_text_plate` Tool 渲染三张 PNG，读取并检查了完整图像，不只检查 schema。目录：

`/var/folders/_n/bw9m8nhd4h70rt6gjsj_d7c80000gn/T/short-drama-text-plate-review-xr_czyxo/`

| 文件 | 观察 |
| --- | --- |
| `original-empty-rows.png`，1536×2176 | 重现失败请求的实际文字、空行与画布设置。中文完整、笔画清楚；正文上方与签字区留白保留，双签字栏分置下半部。原先合理的空行意图现在可执行。 |
| `pending-no-empty-rows.png`，1440×2032 | 对照去空行请求的文字与位置设置。文字完整，但标题、正文和双签字栏均挤在上部；不满足“下半部签字区”的版式用途。待处理请求可恢复执行，但仍需 Agent 看实际结果后按意图改布局。 |
| `explicit-blocks.png`，1536×2176 | 标题居中，正文位于约 25% 高度，双签字栏约 62%/77% 高度；横线为独立几何元素，长度、间距及写字区域明确。没有额外姓名、签名或正文改动。 |

这些是本地准确平面内容样例。它们尚未证明摄影透视、纸张/手部接触、远景文字可读性或视频过程中的稳定性。实际关键帧和视频仍需按最终出现的媒体检查。临时文件供本轮人工视觉复核，不写入 debug 项目。

## 验证

在 `backend` 执行：

```sh
.venv/bin/python -m pytest tests/test_text_plate_tool.py tests/test_text_plate_layout.py tests/test_asset_inspection.py -q
.venv/bin/pyright short_drama/image_models.py short_drama/asset_workbench.py short_drama/asset_inspection.py short_drama/models.py tests/test_text_plate_layout.py tests/test_text_plate_tool.py
```

结果：51 项测试通过（4.65 秒），上述 Pyright 检查 0 errors，`git diff --check` 通过。覆盖真实空行失败、待处理请求兼容、实际像素落点/写字留白、溢出无半成品、具体字段错误、内部标点与金额/姓名变化、物理 Asset 当前媒体与 brief 保留，以及视觉检查按准确表面内容职责注入。父任务也独立查看了原空行版与 blocks 版实际 PNG；去空行版没有被记作版式通过。
