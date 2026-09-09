# 可选 ComfyUI：本机连接与真实工作流

2026-09-09 已通过真实服务验收。本机 MiniMax 管理的 ComfyUI 运行在 `http://127.0.0.1:18188`，报告版本为 0.30.0；网页预览和校园案例桌面配置已连接该地址。默认 8188 没有服务，不代表本机没有可复用的 ComfyUI。此前准备的 `comfy-env` 仍缺少依赖，本次直接复用了现有服务，没有安装新依赖或模型。

## 使用方式

1. 保持独立 ComfyUI 服务运行，在 Studio 的“设置与创作方法 → 高级工作流”填写地址并保存连接。
2. 在 ComfyUI 中导出 API 格式工作流 JSON，再在 Studio 导入。可从“打开高级工作流”进入现有服务页面。
3. 为工作流的素材输入选择具体项目文件，其他参数保留工作流原值。点击“执行工作流”后可以离开设置，任务继续执行。
4. 完成结果进入项目资产。需要用于某个镜头时，在镜头编辑中绑定相应素材；不会自动替换原图或改写已生成的视频。

Agent 的 `task.read_workflow` / `task.run_workflow` 通过同一适配层读取、执行和回写。任务记录保存外部 prompt_id，重启续接已提交的任务；提交状态未知时明确说明，不盲目重新提交。

## 本次实际验收

- 原始图片来自已完成的校园案例，在独立验收项目中使用；原 131 秒案例内容未改动。
- 实际执行 `LoadImage → ImageScale → SaveImage`，图片从 1536×1536 等比例缩放为 512×512。上传字节与所选素材一致，输出像素与预期 Lanczos 缩放一致。
- 实际工作流：`workflow-0f5bdbf59a19`；服务 prompt_id：`f045700d-8db1-4faf-be00-bd53ec7a2f9c`。服务返回 success，输出被 Studio 导入为项目资产。
- 页面刷新后连接和完成记录仍可读取；更新后的 macOS 应用重开后保持连接。
- 制作记录导出包含任务记录、精确输入绑定、实际提交图和服务结果，位于 ZIP 的 `workflows/<id>/`。
- 本轮另外修复“提交中”状态的界面跟踪，并补齐工作流 Trace 导出。7 项相关后端检查（含 Pyright）、TypeScript、构建、离线打包和原生应用检查通过。

实际文件：

- [可导入的缩放工作流](../output/product-delivery/comfy-live/已有图片缩放.json)
- [页面验收](../output/product-delivery/comfy-live/ui-acceptance.json)
- [实际媒体结果与服务记录](../output/product-delivery/comfy-live/media-acceptance.json)
- [桌面重开检查](../output/product-delivery/comfy-live/native-reopened.json)
- [该次工作流的制作过程包](../output/product-delivery/comfy-live/production-trace.zip)

## 使用边界

ComfyUI 是独立可选服务，不随 Studio 启动或分发。可执行能力取决于该服务已经安装的节点和模型；本次验证的是已有图片处理，没有声称超分、插帧或所有自定义节点都已验证。主制片、LTX 剪辑和 FFmpeg 导出不依赖该服务。

公开插件与本地来源的分发边界见 [NOTICES](../NOTICES.md)。本地上游源码副本仅用于研究与验收，没有复制 MiniMax 私有前端，也没有将 LTX 或其他模型权重放入应用包。
