# 图片计划与目标父图的操作映射

日期：2026-09-12，Asia/Shanghai。针对前置 debug 项目的实际错误做合同与提示修复，没有修改运行项目或调用 Provider；不是新空项目完整验收。

## 实际问题与对照

项目 `project-5b8286dc63e6`，任务 `studio-job-20f086971171`。04:45:39，赵衡近景 Worker 的 `tool-6912f25023ee` 向 `image.enqueue` 传 `operation=REFERENCE_GENERATE`，同时指定既有赵衡图 `asset-version-6a56e078129b` 为 `parent_version_id` 和主身份参考，并保留会议室第二参考。它被本地输入校验拒绝，未产生图像任务或供应商调用。旧错误只有 `/request` 和“creates a first asset version without a target parent”，既未指出具体字段，也让人误以为已有 Asset 不能再做参考生成。

同一轮 04:45:42，许岚在已有 Asset 上使用 `REFERENCE_GENERATE`、`parent_version_id=null` 与真实身份/场景参考，成功创建 `image-task-b0b01fd40807`。赵衡随后使用 `EDIT` 和同一目标父图，于 04:46:06 创建 `image-task-870caac7882d`；只读查询时两任务均已成功。这说明问题是本地父图合同及提示不清，不是参考图或供应商不支持；错误引起了一次额外的修正决策。

记录可定位到运行目录 SQLite 的上述 ImageTask 与 WorkOrder；本地失败 Trace 为 `output/quality-e2e-2026-09-12/runtime/outputs/short-drama-projects/project-5b8286dc63e6/studio-trace/studio-job-20f086971171/20260911T204539454117-6d5f055c-tool.finished.json`。所有读取使用 SQLite `mode=ro` / `query_only=ON`。

## 官方接口与本地采用点

OpenAI 官方 [Image generation guide](https://developers.openai.com/api/docs/guides/image-generation) 区分生成与编辑入口；[Create image edit](https://developers.openai.com/api/reference/resources/images/methods/edit) 说明编辑请求根据文字和输入图像生成结果，并接受多张输入图像。这里仅采用“明确真实图像输入与编辑意图”的合同方法，没有更换当前 GPT-Image-2 模型，也没有把当前官方页面的新模型能力外推给本账户。

本项目 `OpenAIImageClient.create` 已将 `REFERENCE_GENERATE` 和 `EDIT` 都送往现有 image edit 入口，保持有序真实参考；`parent_version_id` 是本地输出素材的目标谱系，不是供应商请求字段。AssetPlan 的制作意图、依赖与实际父图不能混为一谈，更不能把 primary input 自动当成 target parent。没有声称 MiniMax Design 的私有操作命名或实现已公开。

## 最小修改

| 实际请求 | 参考图 | 目标父图 |
| --- | --- | --- |
| `GENERATE` | 无 | 无 |
| `REFERENCE_GENERATE` | 有序、明确职责 | 省略；允许已有 Asset 新增候选 |
| `EDIT` | 有序、明确职责 | 显式选择目标 AssetVersion |

`ImageTaskCreateRequest` 和 Agent 可发现的请求 schema 都补充 operation / parent 字段说明。`REFERENCE_GENERATE` 加父图的错误现定位 `/request/parent_version_id`，直接提示明确派生目标时改 `operation='EDIT'` 并保留父图和参考；若没有目标父图意图则省略 parent。工具不自动修改 operation、不删除父图、不改变既有 Asset 的合法路径。

Builder、Planner 与 `image.enqueue` 描述统一说明：计划 `REFERENCE_GENERATE` 表示带参考制作；执行时明确选择目标父图，实际请求映射为 EDIT。依赖、身份参考、primary input 或目标 Asset 已存在，本身都不要求 parent。该映射不改变创作目标，也不要求为参数关系重写整份计划。没有新状态机、自动生成、审批或固定流程。

## 验证

在 `backend` 运行：

```sh
.venv/bin/python -m pytest tests/test_image_operation_mapping.py tests/test_short_drama_assets.py tests/test_text_plate_states.py tests/test_text_plate_append.py tests/test_openai_image_client.py -q
.venv/bin/pyright short_drama/image_models.py short_drama/providers/openai_image_client.py short_drama/tools/image/__init__.py tests/test_image_operation_mapping.py tests/test_text_plate_states.py
```

61 项通过（7.43 秒），Pyright 0 errors / 0 warnings。新增真实 Registry 测试先重现错误并确认任务清单、ProjectDocument 和参数均不变、Fake Provider 调用为零；再在同一已有 Asset 上验证无 parent 的参考生成与明确 parent 的 EDIT 都能正常入队，均未提交 Provider。第二项核对两个实际可发现 schema 的说明；既有素材、字版、HTTP adapter 回归共同通过。

运行中的原错误记录与媒体全部保留。此补丁能消除合同中的歧义和错误定位缺口，是否减少真实下一轮模型返工仍需后续产品执行观察。
