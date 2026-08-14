# Phase 0：LTX Desktop 上游基线

## 目标

Phase 0 只建立可复现的上游工程基线，不改短剧业务逻辑：

- 以 LTX Desktop `v1.1.0` 的提交 `24a8440d7a424c283d7e6dd534cbf78549785090` 为工程历史基础；
- 保留本项目调研、决策和实验文档；
- 验证依赖安装、类型检查、后端测试、OpenAPI 合约、前端构建和 macOS Electron 打包；
- 把上游已有问题与本项目新增回归分开记录。

## 当前环境

| 项目 | 当前值 |
| --- | --- |
| 操作系统 | macOS 26.6.1，Apple Silicon arm64 |
| Node.js | 24.18.0 |
| pnpm | 上游指定 10.30.3，由 npm 通过公司 Feed 运行 |
| uv | 0.11.25 |
| Python | 3.13.12；`backend/.venv` 与应用内置 Python 均已验证 |
| Xcode Command Line Tools | 已安装 |
| 可用磁盘 | 约 241 GiB |
| 系统 FFmpeg | 当前 PATH 中没有；上游声明开发期可回退到 `imageio-ffmpeg` |

## 公司 Package Feed

微软管理的开发机已经给 npm 和 pip 配置了内部代理：

- npm：`https://packagefeedproxy.microsoft.io/npm/`；
- PyPI：`https://packagefeedproxy.microsoft.io/pypi/simple/`。

Corepack 不继承 npm 的 registry，并且内部 npm Feed 不提供 Corepack 查询的
`/pnpm/<version>` 元数据路径。因此本机不使用 Corepack 下载 pnpm，而是让已经配置好
内部源的 npm 运行锁定版本。pnpm 本身仍严格使用上游要求的 `10.30.3`：

```bash
pnpm_phase0() {
  npm_config_registry=https://packagefeedproxy.microsoft.io/npm/ \
    npm exec --yes --package=pnpm@10.30.3 -- pnpm "$@"
}

pnpm_phase0 --version
pnpm_phase0 install --frozen-lockfile
```

上游 `uv.lock` 把 PyPI wheel 的公网制品 URL 一并锁入文件；只设置
`UV_DEFAULT_INDEX` 后执行 `uv sync --frozen`，仍会访问 `files.pythonhosted.org`。
为保留上游锁文件不变，先导出锁定版本，再让 `uv pip` 从公司 PyPI Feed 获取相同版本：

```bash
cd backend
uv venv --python 3.13.12
uv export --frozen --all-extras --no-emit-project --no-hashes \
  --format requirements-txt \
  --output-file /tmp/ltx-phase0-requirements.txt
uv pip sync /tmp/ltx-phase0-requirements.txt \
  --python .venv/bin/python \
  --default-index https://packagefeedproxy.microsoft.io/pypi/simple/
uv pip check --python .venv/bin/python
cd ..
```

这一步在当前平台安装了 80 个兼容包；Git 固定提交仍从 GitHub 获取。此后上游
`backend:test` 内部的 `uv sync --frozen` 可直接校验已有环境，不需要修改 `uv.lock`。

## 完整验证命令

以下命令接在上面的 `pnpm_phase0` 函数和依赖安装之后：

```bash
pnpm_phase0 typecheck
pnpm_phase0 openapi:check
pnpm_phase0 backend:test
pnpm_phase0 build:frontend

PIP_INDEX_URL=https://packagefeedproxy.microsoft.io/pypi/simple/ \
  UV_DEFAULT_INDEX=https://packagefeedproxy.microsoft.io/pypi/simple/ \
  pnpm_phase0 prepare:python

CSC_IDENTITY_AUTO_DISCOVERY=false pnpm_phase0 build:fast
```

`build:fast` 生成 ad-hoc 签名、未公证的 macOS unpacked app，用于本地工程基线和
启动冒烟；正式 Developer ID 签名、公证及发布凭证不属于 Phase 0。

## 本地 unsigned 构建修复

原始 `build:fast` 在设置 `CSC_IDENTITY_AUTO_DISCOVERY=false` 后仍保留
`hardenedRuntime: true`。electron-builder 会退回 ad-hoc 签名，而 macOS 26 会拒绝
加载不同 ad-hoc 签名的 Electron Framework 和 Python 动态库，导致应用在入口处退出。

`scripts/create-installer.sh` 做了一个仅限本地 unsigned 路径的修复：同时满足 macOS、
`--unpack` 和 `CSC_IDENTITY_AUTO_DISCOVERY=false` 时，向 electron-builder 传入：

```text
--config.mac.hardenedRuntime=false
--config.mac.notarize=false
```

正式签名构建仍使用 `electron-builder.yml` 中原有的 hardened runtime、entitlements 和
notarization 配置。修复后应用和包内 Python 均可启动，`codesign --verify --deep
--strict` 也通过。

## 验证结果

| 检查 | 状态 | 结果 |
| --- | --- | --- |
| 上游提交与 tag | 通过 | `v1.1.0` 指向目标提交，当前工程历史直接继承该提交 |
| 调研文档保留 | 通过 | 文档已叠加到上游历史，根目录没有嵌套仓库 |
| Git 对象完整性 | 通过 | `git fsck --full` 无错误 |
| 变更范围 | 通过 | 无前端、Electron 或后端业务逻辑变更；除文档外只有上述本地打包脚本修复 |
| Python 锁文件 | 通过 | `uv lock --check --offline` 解析 109 个包且锁文件一致 |
| 生成契约与锁文件存在性 | 通过 | OpenAPI JSON/TypeScript、`uv.lock` 与 `pnpm-lock.yaml` 均存在 |
| Shell/Node 脚本语法 | 通过 | 上游 setup/build/package shell 脚本及 Node 启动脚本通过语法检查 |
| Python 源码编译检查 | 通过 | Python 3.13.12 对 `backend/` 完成 `compileall` |
| Node 依赖安装 | 通过 | 公司 npm Feed 下载并安装 481 个包，pnpm 10.30.3 |
| Python 依赖安装 | 通过 | 公司 PyPI Feed 安装当前平台 80 个包，`uv pip check` 无冲突 |
| TypeScript/Pyright | 通过 | `tsc --noEmit` 通过；Pyright 为 0 errors、0 warnings |
| OpenAPI 合约 | 通过 | 重新生成 JSON/TypeScript 后与仓库版本无差异 |
| 后端测试 | 通过 | 567 项全部通过，用时 48.23 秒 |
| 前端构建 | 通过 | Renderer、Electron main、preload 均成功构建 |
| 内置 Python | 通过 | Python 3.13.12、PyTorch 2.11.0、MPS、FastAPI、Diffusers 和 ltx-pipelines 自检通过；约 0.89 GB |
| Electron unpacked app | 通过 | 输出 `release/mac-arm64/LTX Desktop.app`，约 1.2 GB；深度签名校验通过 |
| 应用启动冒烟 | 通过 | Electron 启动，包内 Python 启动 FastAPI/Uvicorn，前端确认后端就绪；未下载模型，随后正常退出 |

## 非阻断的上游现状

- npm 运行 pnpm 时会提示项目配置 `minimum-release-age` 不是 npm 自身配置；不影响 pnpm。
- Browserslist 数据已有 7 个月未更新；不影响构建。
- Renderer 主 JavaScript 约 1.03 MB，Vite 给出大于 500 kB 的拆包建议。
- electron-builder 在 Node 24 下给出 `DEP0190` 警告。
- `electron-builder.yml` 引用了未生成的 `python-deps-hash.txt`。macOS 直接使用包内 Python，
  `isPythonReady()` 在 macOS 固定返回 ready，因此本次启动不受影响；Windows/Linux 后续再验证。
- unpacked 包没有 `app-update.yml`，启动时自动更新检查打印 `ENOENT`，不影响应用和后端启动。
- 应用图标运行时查找了 `Contents/MacOS/resources/icon.png`，该路径不存在，并伴随 Electron
  菜单的 `representedObject` 警告；均未阻断窗口和后端。
- 本次冒烟时系统只剩约 7 GB 可用内存，因此上游本地 LTX 推理策略判定为
  `unsupported`。Phase 0 没有下载约 150 GB 模型，也没有执行本地视频生成。
- 后端不是可直接 editable-install 的标准单包结构；setuptools 会报告多个顶层包。
  上游通过从 `backend/` 直接运行模块工作，类型检查、测试、OpenAPI 和打包均已通过。

## Phase 0 结论

上游工程已在当前 Apple Silicon 开发机完成从依赖安装到桌面应用启动的闭环。基线具备
继续进行短剧业务架构和 Phase 1 实现的条件；LTX 自带模型和产品流程只是参考底座，
不等于本项目最终的视频生成方案。
