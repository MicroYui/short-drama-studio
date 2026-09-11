# H3 准备输入与实际文件一致性

日期：2026-09-12。范围：`route.prepare_h3_group(enqueue=false)` 的准备边界、媒体读取与缓存依赖。此次问题来自只读代码核对及独立本地复现，**没有证据表明当前制作案例已触发该问题**。未修改当前制作数据库，未调用真实语言、图片或视频 Provider。

## 发现与最小修复

请求同时带 `asset_version_id` 和 `source` 时，现有编译器会按该 AssetVersion 的当前文件解析真正输入。例如，请求仍写旧路径 A，但选中版本当前指向 B，编译与 Route 实际使用 B。此前组合操作却先用 A 保存 `input_context.media_inputs` 和 `media_file_states`，之后才由编译器解析 B，导致：

- A 尚存在：准备成功，但缓存依赖记录 A，真正使用的 B 发生变化可能无法据此发现。
- A 已删除而 B 仍可用：在编译前误报旧路径缺失。

`backend/short_drama/h3_group_operation.py` 现在在媒体选择后立即复用 `_resolve_approved_asset_inputs(document, media)`，随后对同一份解析结果记录路径、大小、修改时间，传给编译器并保存 Route。该既有解析器按传入顺序返回媒体；当前明确选中的可用 DRAFT 继续可用。未增加审批、文件哈希或新的缓存系统。

旧操作若曾保存不一致输入，按 `operation_id` 恢复会明确返回 `INPUTS_CHANGED`；以当前镜头/组准备新的操作即可。历史提示词和 Route 保留，不把旧依赖偷偷改成已验证的新依赖。当前机制仍是路径、size、mtime 判断，不声称识别“字节已变但大小和修改时间刻意保持相同”的文件替换。

## 一手参考及本地采用点

DVC 官方文档说明，其 run cache 将命令、依赖内容或参数值作为运行匹配的依据，并从已有运行中复用结果。[DVC Run Cache](https://dvc.org/doc/user-guide/pipelines/run-cache)、[DVC Internal Files — Run cache](https://dvc.org/doc/user-guide/project-structure/internal-files#run-cache)。

这里采用的是“缓存依据必须对应实际执行输入”这一原则：先解析当前版本的真实文件，再记录本项目既有文件状态。未移植 DVC 的内容哈希或运行缓存实现；也不据此声称知道 MiniMax Design 的私有缓存方式。

## 不生成视频的实际边界

真实入口为 `tools/route/__init__.py` → `ShortDramaService.prepare_h3_group` → `h3_group_operation.prepare_h3_group`。保存当前生成组、编译提示词、保存 Route 后，只有本次请求的 `enqueue` 为真才调用 `execute_h3_production_route_plan`。`enqueue=false` 不创建新的 GenerationTask，不调用 H3 Worker，也不提交视频。

这不等于完全没有模型调用：默认 `LANGUAGE` 会调用语言提示词编译器；过长提示词可触发已有的局部压缩调用。显式 `H3_CONTEXT_IR` 会调用独立提示词服务；Team 的异步提示词恢复保留原 `enqueue=false`，不会改成视频入队。此前已存在的视频任务不因一次准备请求而取消，读取旧操作也可能显示它们的已有状态。

## Agent 可核对的已接通入口

- `route.get_h3_group_operation`：准备状态、组内镜头 ID、生成组/Route 引用、`input_strategy.resolution/aspect_ratio`、按顺序列出的角色及用途、空或已有 `task_ids`。
- `route.get_h3_plan`：真实保存的完整提示词、`decisions[].media_inputs` 中的实际路径/URL与顺序、AssetVersion/subject/role、`resolution`、`aspect_ratio` 和生成时窗。无需重新编译或调用 Provider。
- `route.get_generation_unit_context`：本组当前镜头事实、对白、时窗和实际可用媒体候选路径。它是候选清单；最终选择和发送顺序以已保存 Route 为准。

固定比例的 DIRECT/REFERENCE 可明确准备 `2K` / `9:16`。硬首/尾帧路线依供应商合同使用 adaptive/省略比例，实际画幅由输入帧决定；不能把项目目标 9:16 当作实际帧尺寸已经符合要求。准备成功也不等于画面质量、远程 URL 可取回或未来视频效果已通过验收。

## 本地复现与回归

`tests/test_h3_group_operation.py::test_prepare_tracks_canonical_local_files_before_cache_and_compilation` 使用现有完整组 fixture、两张本地 PNG、Fake 语言与视频客户端，参数化覆盖旧路径存在/不存在。补丁前两例分别复现“记录 A 而非 B”和“误报 A 缺失”；补丁后验证：

- 当前 DRAFT 文件按用户给出的逆序保持到保存输入、实际编译结果及 `route.get_h3_plan`。
- 本组提示词输入、准备回执和 Route 均为 2K / 9:16。
- 仅旧路径变化不会重编；实际文件 size/mtime 变化会让旧操作报告 `INPUTS_CHANGED`，新准备只再编译一次。
- 实际文件删除时在编译前报具体缺失路径。
- 全过程 `task_ids=[]`，GenerationTask 与 H3 provider-run 列表为空，Fake 视频提交数为零。

受影响回归同时包含原组合准备/重启/不入队、只读组媒体上下文、媒体顺序保存与读取、2K/各比例输出的已有测试。在 `backend` 执行：

```text
.venv/bin/python -m pytest tests/test_h3_group_operation.py tests/test_h3_generation_unit_language.py tests/test_h3_output_options.py -q
51 passed in 9.90s

.venv/bin/pyright short_drama/h3_group_operation.py tests/test_h3_group_operation.py
0 errors, 0 warnings, 0 informations
```

`git diff --check` 通过。本次没有为准备检查提交任何真实视频。
