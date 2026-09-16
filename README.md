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

当前内部候选 **r7** 面向 **macOS arm64**，位于 [Short Drama Studio.app](<output/commercial-delivery-2026-09-16/package-final-r7/mac-arm64/Short Drama Studio.app>)，包含本地 Python 与导出运行时，当前视频生成只使用 MiniMax H3。包内 726 份后端文件与构建输入逐字节一致，前端及导出运行时相对 r6 保持；见[r7 构建记录](output/commercial-delivery-2026-09-16/native-final-r7-build.md)。正常升级后，四个项目的正式内容、八份完整任务保持；恢复时实际变更的 Agent 记忆及派生上下文也已逐项说明，见[r7 升级核对](output/commercial-delivery-2026-09-16/r7-upgrade/reviewed-differences.md)。

用户本轮确认“两部新片验收没问题”，并对原片/H3 候选顺序对照回复“两段都可以”。原 H3 返工任务已在 r7 继续至完成：只将镜头 02 的 5.545 秒替换为指定候选源窗，微调该句字幕，并交付 1920×1080 / 24 fps 的净版和字幕版；原生预览、字幕显示、回零和重播已验证。候选按用户确认记为可用，不宣称比原片更优或由模型完成听审。r6 采用成功后的重复读取/采用及 r7 回执修复保留在[续跑记录](output/commercial-delivery-2026-09-16/stage4-real/dialogue-reverse-h3-rework/resumed-acceptance/README.md)，原生观察范围见[播放记录](output/commercial-delivery-2026-09-16/stage4-real/dialogue-reverse-h3-rework/resumed-acceptance/native-playback.md)。两版文件均完整解码 502 个视频帧、音轨无损坏，采样接点与字幕核验通过。本轮五阶段适用验收已完成，完整分项状态见[产品成熟化实施与验收](docs/product-commercial-delivery.md)。

早期空用户目录、r3 完整源副本离线后的原生导入/编辑/导出、r4 播放与 r5 有限任务保留各自验收范围，未将增量升级写成重复执行全部流程，见[迁移验收](output/commercial-delivery-2026-09-16/current-native-migration/README.md)及[r5 有限验收](output/commercial-delivery-2026-09-16/stage4-real/prop-handover-r5-context-check/README.md)。r5 已完成一次约 3 秒麦克风实测，保存 2.76 秒 WAV、角色绑定、重开与试听通过；用户确认“麦克风看起来解决没再遇到”，当前录音链路通过。首次授权弹窗及拒绝路径本次未触发、未覆盖，不阻挡本轮，见[麦克风实测](output/commercial-delivery-2026-09-16/microphone-native-acceptance/README.md)。

这是本机验收候选，未签名、公证或公开发布。用户明确暂不发行，签名、公证、发行授权与 FFmpeg 对应源码交付暂缓，不阻挡本轮本地验收，也未勾选为完成；见[分发条件复核](docs/research/2026-09-16-commercial-distribution-review.md)。Windows/Linux 未在本次环境打包验收。

普通启动使用 `~/Library/Application Support/ShortDramaStudio/` 保存项目和设置。先新建作品，在“设置与创作方法”配置语言、图片和视频服务；媒体 Provider 独立配置。`STUDIO_APP_DATA_DIR` 可指定独立数据目录，便于测试和演示。

首次配置、编辑导出、项目收集与迁移、保存和中断恢复，见[本地使用与恢复](docs/studio-local-use-and-recovery.md)。日常项目保存在 SQLite，媒体保留为文件；备份和搬迁项目时使用包含媒体的项目包，不能只复制数据库。

打开当前候选可执行：

```sh
open "output/commercial-delivery-2026-09-16/package-final-r7/mac-arm64/Short Drama Studio.app"
```

应用包不包含 API 凭据、模型权重或案例文件。原校园案例和 `scripts/launch-studio-case.sh` 保留历史用途；该脚本仍指向旧包及 `output/product-delivery/native-case-data`，不是当前候选的启动入口。旧案例媒体通过原有本机路径读取，迁移与验证记录见[原 131 秒案例](docs/50-complete-film-agent-case-trace.md)及当前交付清单。

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

[产品定义](docs/product-prd-and-roadmap.md) · [工程指南](AGENTS.md) · [当前成熟化验收](docs/product-commercial-delivery.md) · [本地使用与恢复](docs/studio-local-use-and-recovery.md) · [历史交付记录](docs/product-delivery-checklist.md) · [Skills 与模型](docs/agent-skills-and-model-redesign.md) · [原 131 秒案例](docs/50-complete-film-agent-case-trace.md)

原校园案例包含用户提供的图片、内容确认、开发修复和多次恢复。当前产品验收另行记录；不把它描述为一次无人工参与的稳定成功，也不把缺失的历史 usage 当作 0。独立稳定性重复实验继续暂缓。

项目复用及再分发边界见 [NOTICES](NOTICES.md)，LTX 上游历史介绍仅作为[维护资料](docs/upstream-ltx-readme.md)。
