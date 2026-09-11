# 同一纸张的精确文字状态

日期：2026-09-12，Asia/Shanghai。

## 真实问题与边界

debug 项目 `project-5b8286dc63e6` 的纸笔 Worker 在 03:26:48 调用 `image.compose_text_plate`，以已经排好版的空白 v3 `asset-version-318e8d7fb28c` 为父版本，增加复核栏的“赵衡”。该调用为 `specialist-turn-9cb7d576ea4c41c59f245c890e444661`，work order 为 `work-order-12f15f76ccb84cf1b6346a8ab5374c03`。

Tool 拒绝的原因是渲染文案多了姓名。资产 brief 当时已恢复为 `PHYSICAL_MASTER`，固定文案为“情况说明 / 本次报价出现错误，现补充核对。 / 经办 / 复核”；连续性说明也已经包含双空白、仅赵衡签复核、随后许岚签经办等剧情状态。旧合同却要求所有候选全文等于这一份全局固定文案，没有地方表达本次明确新增的签名。让 Agent 反复改共享 brief 会破坏早期候选的语义，是合同缺口。

这与之前旧 pending 请求的版式问题不同：v3 已用 `text_blocks` 把两栏移至下半部，并有真实产品 QA 记录；本轮失败没有保存单签版本。后续真实 v4 是空白物理纸张摄影，根代理已在 [04-first-assets-review.md](04-first-assets-review.md) 记录其人工观察；它不等于本轮单签状态完成，也没有被本修复自动审核或采用。

本轮只修改代码、文档与本地测试。没有修改运行数据库，没有发送图片或视频 Provider 请求，没有调用运行项目的 QA。

## 采用的方法

Adobe 官方 Layer Comps 将同一文档的不同呈现状态明确命名，并记录选定的可见性、位置、外观等属性，可逐个应用及导出。这支持“明确记录本次状态及影响范围”的普通制作方法；它不是关于 MiniMax 私有实现的证据，也不证明 Photoshop 的所有文字数据天然互相独立。[Adobe 官方说明](https://helpx.adobe.com/photoshop/using/layer-comps.html)

本地沿用已有 Asset / AssetVersion，不新增状态系统。具体字版的状态随该版本保存；固定道具事实留在共享 brief。准确字版继续由现有 Pillow 渲染，不让图片模型猜汉字。

## 实际合同与接线

`image.compose_text_plate.request.content_state` 是可选对象：

```json
{
  "name": "仅复核赵衡已签，经办空白",
  "required_visible_text": [
    "情况说明",
    "本次报价出现错误，现补充核对。",
    "经办",
    "复核",
    "赵衡"
  ]
}
```

它声明本候选的完整准确文字。每个 brief 固定条目必须作为不变条目保留且顺序相同，新增内容另列条目；绘制块可以按同一完整文字换行。实际 `text_blocks` 或 `title/exact_lines` 仍须精确匹配该清单。空行、行末标签冒号和装饰线继续只算布局；内部标点、标识符、数字、金额与姓名不能悄悄变化。未传状态时仍按原 brief 校验，descriptor 和 layout_description 不会自动授权新增正文。

| 环节 | 本轮处理 |
| --- | --- |
| 本地字版 | `AssetTextPlateContentState` 保存在具体 `AssetVersion.text_plate.content_state`；PHYSICAL_MASTER PROP 生成同 Asset 下的 PHYSICAL_SURFACE_CONTENT DRAFT，不改 brief/current/canonical 或旧候选。 |
| AssetPlan | Intent 与 Spec 都接受相同 state、text_blocks、lines、画布范围、对齐和描边；`to_compose_request` 统一转换。计划校验与 Tool 共用文字合同，去除要求把物理载体改成屏幕图版的修复建议。 |
| 完成登记 | 校验实际可用候选及其内容状态；允许物理纸张的确定性表面版本。把空白版登记为单签 work 会具体报错，其他状态不失效。 |
| 图像派生 | `_resolve_provider_inputs` 读取实际选中字版；真实 OpenAI multipart prompt 按上传图编号附上字版用途、准确文字与明确状态，仍服从本次 reference role / authority / purpose。原始 Agent prompt 不被改写。 |
| 视觉观察 | 本地字版按自己的状态检查；派生摄影 QA 的 reference image_map 带上实际所用字版状态。摄影候选自身不会被错误标成确定性 text_plate。 |

来源状态不自动变成摄影候选的最终状态。若多张参考属于不同剧情状态，按本次明确用途选择，不能把姓名或状态简单合并；缺少目标状态时应保留不确定性。本合同目前用于固定文字保留、明确状态增补，不能借状态字段替换或删除固定正文，也不从自由描述猜测状态。

## 本地验收

新 `backend/tests/test_text_plate_states.py` 使用真实 v3 的 1440×2032 几何，覆盖：

- 原 v20 式请求未声明状态仍被拒绝，并返回版本级状态修复提示；明确声明后可生成单复核签名，再生成双签。
- PNG 像素差只出现在指定签名区域；两栏分别位于页面下半部。三张文件、旧 AssetVersion、brief/current/canonical 均保持，重开服务后仍可使用。
- 已声明状态仍不能改字、漏固定正文、调换固定条目、把姓名混进固定条目，实际绘制不能出现未声明姓名。
- 保存 AssetPlan、读取其 intent、转换到真实 Tool、登记 PHYSICAL_SURFACE_CONTENT 完成与错误状态拒绝均有覆盖。
- Fake 图像执行器收到实际字版 PNG 和状态；真实 HTTP adapter 使用内存 Fake transport，确认状态进入实际 multipart prompt。派生摄影 QA 的 Fake 语言输入包含准确来源状态，摄影类型保持正确。这是接口与输入检查，不是付费摄影效果验收。

已复用本地测试输出，并人工查看双签样例：标题、固定正文与两个姓名正确，姓名在对应横线上方；这是字形/位置底稿，没有伪称为手写摄影或连续书写视频。

- [空白样例](../../../output/quality-e2e-2026-09-12/text-plate-state-samples/blank.png)
- [仅复核签名](../../../output/quality-e2e-2026-09-12/text-plate-state-samples/reviewer-signed.png)
- [双签样例](../../../output/quality-e2e-2026-09-12/text-plate-state-samples/both-signed.png)

测试命令（backend 环境）：

```text
.venv/bin/python -m pytest tests/test_text_plate_states.py tests/test_text_plate_layout.py tests/test_text_plate_tool.py tests/test_asset_inspection.py tests/test_openai_image_client.py -q --tb=short
72 passed in 6.37s
.venv/bin/python -m pytest tests/test_short_drama_assets.py tests/test_asset_plan_reconciler.py -q --tb=short
25 passed in 3.84s
```

旧 AssetPlan 错误信息断言由根代理同步，并随其 DAG/下游套件通过。新增字段需要根代理统一生成 OpenAPI 并重启后才会出现在运行产品中。真实新项目端到端制作、签后摄影及视频保持仍须后续产品任务验收；本 debug 修复不冒充新项目完整交付。
