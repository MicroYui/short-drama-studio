# AI 短剧项目约束

- 按普通产品工程和普通实验方法推进，采用满足正确性、可复现性与公平比较所需的最小机制。
- 文档正常随项目更新，使用 Git 自然保留历史；不设置额外的冻结层级、文档 SHA 锁定、审批门或证据链。
- 只需清楚区分已经确认的决定与仍需实验的结论。实验得到结果后，直接更新相关设计和实现。
- 不主动扩展为安全审计、对抗评测、隐私框架或密码学证明系统；只有出现具体且在范围内的风险时才增加相称措施。

## 产品北极星

当前权威产品定义见 `docs/product-prd-and-roadmap.md`。目标是一个单用户本地 AI Agent 视频制片工作台：上层采用 MiniMax Design 类似的 Agent 对话和语义节点画布，Agent 完成剧本、分镜、核心资产、图片、视频、音频和初步时间线；高级媒体工作流可展开 ComfyUI；最后复用 LTX Desktop 中不依赖 LTX 模型的多轨编辑、拖动裁切、换 Take 和 FFmpeg 导出。

必须保持以下边界：

- `ProjectDocument + versioned Artifacts` 是唯一内容真相；语义画布、ComfyUI 图和 LTX 时间线是不同视图，不建立三套项目状态。
- 优先直接复用成熟开源实现：LTX Desktop 继续作为应用与编辑底座；MiniMax Design 开源 ComfyUI plugin 在几分钟成片 PoC 后移植；MiniMax-H3 公开 Skills 和已保存的 Design Workflow 原文通过薄适配器进入现有 Agent Harness。ComfyUI plugin 为 GPL-3.0，Design 安装包 Workflow 未附公开再分发许可；正式发布前保留并处理相应 License、Notices 和再分发边界。
- 当前 LTX Home/GenSpace/VideoEditor 代码仍在仓库但没有挂到产品入口，且其项目主要保存在前端 `localStorage`；不要把“代码存在”写成“短剧产品已接通”。后续以 ProjectDocument 内的版本化 EditorialTimeline 为真相，建立 LTX 前端投影、后端持久化和 Agent-facing 编辑 Tool 桥。
- 不启用、不集成、不分发 LTX 模型权重，LTX 不是本地 Provider。上游 LTX generation/Retake/Extend/IC-LoRA 后端不得声称为当前 Agent 能力；相同用户意图只能映射为 H3 镜头重生成、已接入 Provider 能力或纯编辑操作。
- MiniMax Design 外层语义画布、Main Agent/Team Engine、Hub Gateway 和核心资产编排没有确认开源源码，不复制安装包私有前端代码；学习其产品行为和 Workflow，在本项目已有 `AgentRun / ProductionManager / WorkOrder / ToolRegistry` 上实现等价能力。
- 用户明确不交互或交由 Agent 决定时自主推进；用户明确要求确认时只在对应语义节点停靠；其余由 Agent 基于完整语义判断。不要用正则、关键词路由、固定次数、审批门或 Critic 循环代替判断。
- 实施顺序是：先补齐并验证 Agent 后端制片能力，再跑通几分钟完整视频 PoC；随后接入 ComfyUI，建设语义画布，最后完成 LTX 编辑器双向衔接和一体化桌面体验。PoC 前不为最终界面提前重写大规模前端。
- 普通单元测试、集成测试、实际播放和导出检查用于保证软件能工作；不建设新的 QA 产品、对抗评测或盲审体系。

# LTX Desktop 上游工程指南

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

LTX Desktop is an Electron app for AI video generation using LTX models. Three-layer architecture:

- **Frontend** (`frontend/`): React 18 + TypeScript + Tailwind CSS renderer
- **Electron** (`electron/`): Main process managing app lifecycle, IPC, Python backend process, ffmpeg export
- **Backend** (`backend/`): Python FastAPI server (port 8000) handling ML model orchestration and generation

## Common Commands

| Command | Purpose |
|---|---|
| `./scripts/pnpm-project.sh dev` | Start dev server (Vite + Electron + Python backend) |
| `./scripts/pnpm-project.sh dev:debug` | Dev with Electron inspector + Python debugpy |
| `./scripts/pnpm-project.sh typecheck` | Run TypeScript (`tsc --noEmit`) and Python (`pyright`) type checks |
| `./scripts/pnpm-project.sh typecheck:ts` | TypeScript only |
| `./scripts/pnpm-project.sh typecheck:py` | Python pyright only |
| `./scripts/pnpm-project.sh backend:test` | Run Python pytest tests |
| `./scripts/pnpm-project.sh build:frontend` | Vite frontend build only |
| `./scripts/pnpm-project.sh build` | Full platform build (auto-detects platform) |
| `./scripts/pnpm-project.sh setup:dev` | One-time dev environment setup (auto-detects platform) |

Run a single backend test file via pnpm: `./scripts/pnpm-project.sh backend:test -- tests/test_ic_lora.py`

### Restricted Network and JavaScript Dependencies

- The corporate environment only permits its managed Microsoft sources. External npm registries used by npm, pnpm, Corepack, or `npx` are blocked by IT; treat JavaScript dependency execution as offline by default.
- Do not run bare `pnpm`, `corepack`, `npx`, `npm install`, `yarn install`, or any command that may bootstrap a package manager or download packages unless the user explicitly authorizes it. Use `./scripts/pnpm-project.sh` for project commands; it uses the cached pinned pnpm offline when available and permits a cache miss to reach only the managed Microsoft feed.
- Prefer the dependencies already present in the repository and invoke local binaries directly, for example `./node_modules/.bin/tsc --noEmit` and `./node_modules/.bin/vite build`.
- If a required package or binary is not available locally, report the missing dependency and stop that check. Do not retry against public or alternative registries.
- The command table above documents upstream workflows. In this environment, use equivalent local-binary checks where necessary and report the exact commands that were run.

## CI Checks

PRs must pass: `./scripts/pnpm-project.sh typecheck` + `./scripts/pnpm-project.sh backend:test` + `./scripts/pnpm-project.sh build:frontend`.

## Frontend Architecture

- **Path alias**: `@/*` maps to `frontend/*`
- **State management**: React contexts only (`ProjectContext`, `AppSettingsContext`, `KeyboardShortcutsContext`) — no Redux/Zustand
- **Routing**: View-based via `ProjectContext` with views: `home`, `project`
- **IPC bridge**: All Electron communication through `window.electronAPI` (defined in `electron/preload.ts`)
- **Backend calls**: Always use `backendFetch` from `frontend/lib/backend.ts` for app backend HTTP requests (it attaches auth/session details). Do not call `fetch` directly for backend endpoints.
- **Styling**: Tailwind with custom semantic color tokens via CSS variables; utilities from `class-variance-authority` + `clsx` + `tailwind-merge`
- **No frontend tests** currently exist

## Backend Architecture

Request flow: `_routes/* (thin) → AppHandler → handlers/* (logic) → services/* (side effects) + state/* (mutations)`

Key patterns:
- **Routes** (`_routes/`): Thin plumbing only — parse input, call handler, return typed output. No business logic.
- **AppHandler** (`app_handler.py`): Single composition root owning all sub-handlers, state, and lock
- **State** (`state/`): Centralized `AppState` using discriminated union types for state machines (e.g., `GenerationState = GenerationRunning | GenerationComplete | GenerationError | GenerationCancelled`)
- **Services** (`services/`): Protocol interfaces with real implementations and fake test implementations. The test boundary for heavy side effects (GPU, network).
- **Concurrency**: Thread pool with shared `RLock`. Pattern: lock→read/validate→unlock→heavy work→lock→write. Never hold lock during heavy compute/IO.
- **Exception handling**: Boundary-owned traceback policy. Handlers raise `HTTPError` with `from exc` chaining; `app_factory.py` owns logging. Don't `logger.exception()` then rethrow.
- **Naming**: `*Payload` for DTOs/TypedDicts, `*Like` for structural wrappers, `Fake*` for test implementations

### Backend Testing

- Integration-first using Starlette `TestClient` against real FastAPI app
- **No mocks**: `test_no_mock_usage.py` enforces no `unittest.mock`. Swap services via `ServiceBundle` fakes only.
- Fakes live in `tests/fakes/`; `conftest.py` wires fresh `AppHandler` per test
- Pyright strict mode is also enforced as a test (`test_pyright.py`)

### Adding a Backend Feature

1. Define request/response models in `api_types.py`
2. Add endpoint in `_routes/<domain>.py` delegating to handler
3. Implement logic in `handlers/<domain>_handler.py` with lock-aware state transitions
4. If new heavy side effect needed, add service in `services/` with Protocol + real + fake implementations
5. Add integration test in `tests/` using fake services

## TypeScript Config

- Strict mode with `noUnusedLocals`, `noUnusedParameters`
- Frontend: ES2020 target, React JSX
- Electron main process: ESNext, compiled to `dist-electron/`
- Preload script must be CommonJS

## Python Config

- Python 3.13+ (per `.python-version`), managed with `uv`
- Pyright strict mode (`backend/pyrightconfig.json`)
- Dependencies in `backend/pyproject.toml`

## Key File Locations

- Backend architecture doc: `backend/architecture.md`
- Default app settings schema: `settings.json`
- Electron builder config: `electron-builder.yml`
- Video editor (largest frontend file): `frontend/views/VideoEditor.tsx`
- Project types: `frontend/types/project.ts`
