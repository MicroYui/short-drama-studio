# 57 · 同一次 masked EDIT 的两份实际结果交接

日期：2026-09-12。独立实现分支 `codex/edit-candidates`，基于 `main` 的 `3c67c0b`；未修改运行项目、重启应用或调用真实 Provider。

## 真实缺口

新空项目《最后一版》的 S05 任务 `image-task-0c8f1db69724` 产生默认 masked final `asset-version-f5840f2c4ec4`。11:04:15 的 t35 QA 发现电脑右掌托被 mask 截断。原 Provider 文件实际有完整掌托，但没有可交给 `qa.inspect_asset_version` 的 AssetVersion；t36 先调用 `image.derive_edit_result(PROVIDER_OUTPUT)`，才得到 `asset-version-ce9d9fb301d5`，t37 再检查。

已保存图片的独立复核证明：final 精确按 mask 合成；透明区以外改变像素为 0。截口处 `(610,1070)` 保留的是原底图木桌，覆盖了 Provider 原图中的灰色掌托。原图同时移动了鼠标，因此“有完整掌托”不能推出整张原图可直接采用。本次解决的是已经存在的两个实际文件如何一次交接，**不宣称提高 Provider 本身的图像质量，也不自动扩大 mask**。

## 公开方法与本地取舍

- [Anthropic：Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) 强调按任务设计工具、让返回值提供有意义的信息，并用实际执行记录衡量调用与效果。本地采用点是：完成 masked EDIT 时，返回同次已有两份像素的精确身份，省去仅为取得可引用 ID 的中间登记调用；没有回灌整份历史或默认多发一张视觉输入。
- [Adobe：Nondestructive editing](https://helpx.adobe.com/photoshop/using/nondestructive-editing.html) 说明保留原始图像信息、用 mask 隐藏而不删除像素的编辑方式。本地保留已保存的原始响应，不把合成图冒称原图；这不代表复制 Photoshop 内部状态模型。
- [Pillow：Image.composite](https://pillow.readthedocs.io/en/stable/reference/Image.html#PIL.Image.composite) 的合同是使用同尺寸 mask 合成两幅图。原图、底图和 mask 因此必须分别可追溯；原始像素中存在目标结构，仍不能保证 mask 覆盖了该结构。

继续复用 AssetVersion 比让 QA 新增 `image_task + raw` 的另一套输入类型更小。自动 raw candidate 直接指向已持久化 `.provider-output` 文件，不再复制第二份原文件。每个新的 masked EDIT 只增加一条明确用途的 DRAFT 媒体记录，不增加审批、自动选用或独立版本管理界面。

## 实际合同

`ImageGenerationTask` 新增两个可选字段：

- `provider_output_version_id`：已登记、未应用本地 mask 的同次 Provider 候选；未登记时为 null。
- `provider_output_version_number`：预留/登记的数字。只有 ID 已存在且任务完成时，才作为精确 ArtifactRef 使用。

原 `output_version_id / output_version_number / output_path` 仍表示默认 masked final。新 masked task 入队预留相邻两号，raw 在前、final 在后；完成时同一次 `save_project` 先追加 raw、再追加 final，随后保存任务成功状态。当前选择、列表末项及最大版本号均不会因 raw 登记而指向 raw。不同任务逆序完成时，各自预留编号保留；末项是最后完成的 final，最大号是较后入队的 final，二者均不是 raw。

旧在途任务没有 raw 预留号时，只给尚未保存的这次输出分配新的未占用号码，不改已经存在的版本号。原始文件在 `.provider-output`，final 因预留冲突改文件名不会移动该原始文件。已经完成的旧任务不在 `image.list` 读取时新增媒体；旧 `derive` 入口仍可登记或复用原图。

raw 沿用既有本地原图派生合同：`origin_type=LOCAL_DERIVED`、`image_task_id=null`、`source.model=saved-image-edit-result-v1`，source 记录准确 task、PROVIDER_OUTPUT mode 与原文件位置，`parent_version_id` 指向同次 final。这里 parent 是已有追溯约定，不表示将 final 像素反向处理；AssetVersion 与现有 parent resolver 按 ID 查找，没有“parent 数字必须更小”的限制。raw 可直接通过现有 QA Service 校验；无需放宽“image_task_id 对应 task.output_version_id”的校验。

`image.advance` / `image.recover_output` 成功回执包含 raw 与 final 两个精确版本引用，final 放在引用列表末尾；`image.advance` 对已成功任务直接返回既有记录，既不重发 Provider，也不修改选择。角色可以按需检查 raw，仍需分别判断圈外变化、身份和真实用途。

`image.derive_edit_result(PROVIDER_OUTPUT)` 优先复用已经登记的同一 task/source/mode 原图，包括历史手动登记的原图；不只按文件位置猜来源，不创建重复 DRAFT，不覆盖原 descriptor 或审核状态。相同任务的记录已拒绝、失效或文件缺失时，返回具体问题。旧调用传入不同 descriptor 而原候选存在时，保留原描述是有意的幂等语义。该分支回执提供精确引用，不再一律声称 `created_asset_version_ids`；MASKED_COMPOSITE 仍每次保存明确的新合成候选。

制作项和镜头的完成/选用由现有显式精确版本合同处理。登记这条 raw 记录没有写 AssetPlan 完成字段、reference pack、Shot 或 canonical/approved 指针。恢复路径继续保留恢复期间生效的人工选择。

## 验证

新 `test_image_edit_candidates.py` 包括：

- 真实 Registry 完成回执的双 ID/数字、raw 可直接进入 QA Service/FakeLanguage；被拒绝的 final 不冒充 raw 被拒绝。
- raw/final 顺序与最大编号、重复 advance/derive、重开 Service 的稳定身份与零额外 Provider 调用。
- 已拒绝/失效/缺文件 raw 不通过新建 DRAFT 绕过。
- 历史手动原图按 task/mode/source 复用；同路径其他任务不误用，等价本地路径可规范化匹配。
- 旧在途号码与其他预约共存，不改已保存版本。
- 项目中两版本已原子保存、Task 成功记录尚未保存时中断；恢复找回同一 raw/final，并保留随后的人工作品选择。
- 两个 masked task 逆序完成仍保留各自预留号，默认结果均是 final。

既有原图保存、只合成一次、格式/EXIF、恢复、并发及 Tool 端测试继续运行。旧两处“每次 derive 必新建版本”的断言改为检验复用；原有像素与不重发检查保留。

专项与邻接共 **83 passed / 16.76 秒**；最后补等价本地路径匹配后，9 项专项再验 **9 passed / 2.00 秒**。7 个修改源/测试文件 Pyright **0 errors / 0 warnings**。执行方式（在本分支 `backend` 目录）：

```sh
/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/.venv/bin/python -m pytest tests/test_image_edit_candidates.py tests/test_image_edit_results.py tests/test_image_edit_result_tool.py tests/test_image_edit_guides.py tests/test_short_drama_assets.py tests/test_asset_plan_runtime.py::test_explicit_draft_can_complete_plan_and_unblock_derived_work tests/test_tool_registry_coverage.py -q
```

### 已保存 S05 实图的离线回放

`/tmp/edit-candidates-replay/replay_s05.py` 使用 11:05:56 只读快照中的真实 S05 primary、guide、mask、原始 Provider PNG，以及原 request 的尺寸、prompt、参考顺序和 `mask_feather_pixels=0`；只将项目与媒体 ID 映射到独立 fixture。通过现有本地 `image.recover_output` 路径登记，Provider 实现被设为调用即报错。

结果 `/tmp/edit-candidates-replay/result.json`：

- 0 Provider 调用；本地恢复约 **0.289 秒**，不是端到端提速实测。
- 新 final 的 RGBA 与保存的真实 v6 **逐像素相同**；raw bytes 与保存的原 Provider/v7 来源完全相同。
- raw v5、final v6；raw `image_task_id=null`，默认 task 输出、最大版本号及列表末项仍是 final。
- 恢复前已有选择被保留，未切到 raw；再调用旧 derive 返回同一个 raw ID，项目内容没有变化。

回放仍保留真实 v6 的掌托截口，这是复现和身份交接验证，并非生成了更好的作品。原图的鼠标移动、最终画面可用性及视频动态仍需按实际用途判断。

Arendt 已独立只读复核最终差异：精确来源复用、幂等回执、项目/任务保存间隙恢复和逆序完成均未发现本范围具体阻断；未重复运行测试。

PM 工作集两字段白名单由 Arendt 在独立场次上下文切片集中接入，QA `candidate_creation.masked_edit` 的可选 raw 标识由 root 负责。本提交不修改 `asset_inspection.py`、角色 Skill 或运行数据；合并后的完整模型输入检查由根任务统一完成。
