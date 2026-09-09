# Short Drama Studio

单用户本地 AI Agent 视频制片工作台。用一句话说明想完成什么，在剧本、镜头和成片中查看、直接修改内容；项目资产统一管理，制作助手常驻右侧。只要求完整剧本时，Agent 写完即结束；要求成片时，继续完成所需制作。

## 当前产品

- **剧本**：导入文本、按场次直接编辑、Agent 写作与逐场更新。
- **镜头**：完整分镜设计、逐时段文案、精确 `@` 图片引用及悬停预览，与实际视频放在同一镜头内；可导入或替换片段。
- **项目资产**：多集共享人物、场景、道具和声音；查看具体文件、使用位置与待补内容。
- **成片与剪辑**：复用 LTX 非模型多轨编辑，支持裁切、拆分、移动、变速、音量和字幕；镜头与成片打开同一集的剪辑，保存到后端，实际 FFmpeg 导出。
- **制作助手**：按当前目标和范围工作，显示实际动作、处理定向修改和恢复；语言角色统一 `gpt-6-astra / xhigh`。
- **制作记录**：导出目标、当前内容、动作、模型输入/输出、上下文与用量、素材和成片索引，保留用户补图及开发修复事实。

不启用或分发 LTX 模型权重。ComfyUI 是可选外部服务，可配置地址、导入 API 工作流、绑定素材并回收结果；本机已连接 `http://127.0.0.1:18188`，真实工作流、素材回收和制作记录导出已验收。服务需要独立保持运行，主制片和剪辑流程不依赖它。使用方法见 [可选 ComfyUI](docs/comfyui-local-integration.md)。

## 启动与使用

已验证的本地目标是 **macOS arm64**。应用位于 `output/product-delivery/package/mac-arm64/Short Drama Studio.app`，包含本地 Python 与导出运行时；这是本地开发交付包，未签名、公证或公开发布。Windows/Linux 未在本次环境打包验收。

普通启动使用 `~/Library/Application Support/ShortDramaStudio/` 保存项目和设置。先新建作品，在“设置与创作方法”配置语言、图片和视频服务；媒体 Provider 独立配置。`STUDIO_APP_DATA_DIR` 可指定独立数据目录，便于测试和演示。

打开已保存的校园案例可执行：

```sh
./scripts/launch-studio-case.sh
```

这使用 `output/product-delivery/native-case-data` 的案例副本，不覆盖原始实验数据库。原始媒体仍通过已登记的本机路径读取；搬迁案例时也需带上对应媒体。应用包不包含 API 凭据、模型权重或这些案例文件。

## 本地开发与检查

本环境不从公共 npm 源安装依赖；优先现有二进制和项目包装器。

```sh
./node_modules/.bin/tsc --noEmit
./node_modules/.bin/vite build
node scripts/package-studio.mjs
(cd backend && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q)
```

本地网页调试需分别启动服务与前端：

```sh
LTX_APP_DATA_DIR="$PWD/output/product-delivery/app-data" LTX_PORT=8018 backend/.venv/bin/python backend/short_drama_server.py
STUDIO_WEB_PREVIEW=1 STUDIO_BACKEND_URL=http://127.0.0.1:8018 ./node_modules/.bin/vite --port 5178 --strictPort
```

打包脚本只使用缓存中的固定 pnpm 和已安装 Electron，缺少本地依赖时停止，不自动下载。`vite build` 同时编译共享剪辑导出运行时。只运行后端的剪辑导出或相关测试时，先执行 `node scripts/build-studio-runtime.mjs`；生成的运行时和依赖缓存标识不纳入 Git。

## 设计、结果与来源

[产品定义](docs/product-prd-and-roadmap.md) · [工程指南](AGENTS.md) · [交付验收](docs/product-delivery-checklist.md) · [Skills 与模型](docs/agent-skills-and-model-redesign.md) · [原 131 秒案例](docs/50-complete-film-agent-case-trace.md)

原校园案例包含用户提供的图片、内容确认、开发修复和多次恢复。当前产品验收另行记录；不把它描述为一次无人工参与的稳定成功，也不把缺失的历史 usage 当作 0。独立稳定性重复实验继续暂缓。

项目复用及再分发边界见 [NOTICES](NOTICES.md)，LTX 上游历史介绍仅作为[维护资料](docs/upstream-ltx-readme.md)。
