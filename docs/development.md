# 本地开发

本页介绍 Short Drama Studio 的开发入口、依赖、检查和本地打包。日常创作请从[使用与恢复](studio-local-use-and-recovery.md)开始；产品边界与工程约定见 [AGENTS.md](../AGENTS.md)。

## 环境与依赖

当前桌面包验收平台为 macOS Apple Silicon。仓库保留 Windows/Linux 构建配置，但本轮未完成这两个平台的打包验收。

| 依赖 | 要求与用途 |
| --- | --- |
| Node.js | 建议使用 22.12+；当前 Vite 7 的精确版本范围为 `^20.19.0 || >=22.12.0`。 |
| pnpm | `package.json` 固定为 10.30.3，统一通过 `scripts/pnpm-project.sh` 调用。 |
| Python | 当前工程使用 Python 3.13；依赖定义见 `backend/pyproject.toml`。 |
| Python 环境 | 使用 `backend/.venv`，由 `backend/pyproject.toml` 和 `backend/uv.lock` 管理。 |
| 媒体运行时 | 编辑导出需要共享 Node 运行时与可用 FFmpeg；应用包另含本地 Python 运行时。 |

本仓库的 JavaScript 依赖使用受管理的 Microsoft 源，默认优先本地缓存。包装器命中固定 pnpm 缓存时直接运行；缺少缓存时可能从受管理源获取包管理器。

新环境需要先按组织允许的依赖源准备 `node_modules` 与 `backend/.venv`。已有 `setup:dev` 脚本会安装依赖，且 Python 开发依赖仍包含上游组件，目前未完成空机器上的通用安装验收。脚本中的旧 Node 18 提示不适用于当前 Vite 版本；运行 Studio 无需下载 LTX 模型权重。

安装好 Node.js、Python 和 uv，并确认依赖源可访问后，可以在仓库根目录执行：

```sh
./scripts/pnpm-project.sh setup:dev
```

这个脚本通过项目包装器安装 JavaScript 依赖，再执行 `uv sync --extra dev` 准备后端环境。它会访问依赖源，已有环境不必重复运行。

## 桌面开发

准备好上述依赖后，在仓库根目录运行：

```sh
./scripts/pnpm-project.sh dev
```

Vite 启动前端与 Electron；Electron 自动启动 `backend/short_drama_server.py`。调试入口：

```sh
./scripts/pnpm-project.sh dev:debug
```

普通桌面启动的数据目录为 `~/Library/Application Support/ShortDramaStudio/`。独立测试可通过 `STUDIO_APP_DATA_DIR` 指定目录；不要让多个应用实例同时使用同一份数据目录。

生成服务在应用内配置。应用包不包含 API 凭据、模型权重或案例项目。

## 网页调试

在两个终端中分别执行。以下命令均从仓库根目录开始，使用独立的开发数据目录；端口如已占用，应先调整端口及对应代理地址。

后端：

```sh
LTX_APP_DATA_DIR="$PWD/output/studio-dev/app-data" \
LTX_PORT=8018 \
backend/.venv/bin/python backend/short_drama_server.py
```

前端：

```sh
STUDIO_WEB_PREVIEW=1 \
STUDIO_BACKEND_URL=http://127.0.0.1:8018 \
./node_modules/.bin/vite --host 127.0.0.1 --port 5178 --strictPort
```

打开 `http://127.0.0.1:5178`。网页适合检查内容与 API 交互；Electron IPC、原生权限和随包媒体运行时仍需在桌面端验证。

## 本地检查

以下命令使用已经安装的二进制。前端类型与构建，在仓库根目录运行：

```sh
./node_modules/.bin/tsc --noEmit
./node_modules/.bin/vite build
```

`vite build` 同时构建共享剪辑导出运行时。若只运行后端媒体检查，可先在仓库根目录执行：

```sh
node scripts/build-studio-runtime.mjs
```

Python 检查在 `backend` 目录运行：

```sh
cd backend
.venv/bin/pyright
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q
```

把 `.venv/bin` 放入 `PATH`，让测试启动的子进程也能找到项目中已安装的工具。日常修改优先运行相关测试；完整检查用于需要汇总回归的交付。缺少依赖时先解决具体缺项，不自动换用公共源。

## 本地打包

macOS arm64 的现有本地打包入口为：

```sh
node scripts/package-studio.mjs
```

此命令要求前端与 Electron 构建产物、已准备的 Python 运行时和固定 pnpm/Electron 缓存就绪。它只执行打包，不负责构建或初始化依赖。缺少固定 pnpm 缓存时会停止，不自动下载。产物位于 `output/product-delivery/package/`。

这一路径输出未签名、未公证的本地应用目录。公开发行的剩余工作见[分发说明](research/2026-09-16-commercial-distribution-review.md)。本机 `output/` 链接不是公开下载地址；普通 `build` 和 `setup:dev` 脚本可能安装依赖。

## 数据与工程入口

| 领域 | 入口 |
| --- | --- |
| 产品界面 | `frontend/features/short-drama/StudioWorkspace.tsx` |
| 剪辑适配 | `frontend/features/short-drama/StudioEditor.tsx` |
| 桌面进程 | `electron/main.ts`、`electron/python-backend.ts` |
| 本地 API | `backend/short_drama_server.py`、`backend/_routes/studio.py` |
| 项目与持久化 | `backend/short_drama/service.py`、`repository.py`、`studio_service.py` |
| Agent 运行 | `backend/short_drama/production_manager.py`、`agent_runtime/` |
| 制片方法与能力 | `backend/short_drama/skills/`、`tools/`、`providers/` |

项目内容与运行状态以 SQLite 为准，媒体以文件保存。搬迁项目使用包含媒体的项目包；仅复制数据库不会带上视频与图片。`output/` 中的运行数据、构建包和验收记录不随 Git 源码提交。

架构、存储和调试记录见[研究文档总览](README.md)、[Agent 生命周期](agent-harness-context-and-lifecycle.md)和[存储改进](research/2026-09-16-storage-improvements.md)。历史设计保留当时的背景与结论，当前行为以产品定义、现行代码与最新验收记录为准。
