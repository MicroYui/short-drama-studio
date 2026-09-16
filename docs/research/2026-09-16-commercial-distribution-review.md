# 商业分发条件：实际随包内容与 Provider 条款核查

核查日期：2026-09-16。范围是当前 macOS 短剧发行配置、已经准备的 `python-embed`、方法来源及当前三条 Provider 配置链路。此次只读核查没有启动旧项目、调用 Provider、接受条款或发布安装包；没有逐项审计全部间接依赖。下文区分本地事实、公开许可、账户合同未知和建议。

当前范围更新（2026-09-16）：用户明确暂时不需要发行，公开发行工作暂缓。签名/公证、发行授权及 FFmpeg 对应源码交付不再阻挡本轮本地产品验收；原发现与未完成事实保留，不记为通过。其余本地功能和作品质量验收范围不变。

当前结论：继续本地产品验收；尚不能把现有安装包直接称为已准备好公开商业分发。未启用 LTX 推理代码的随包排除、实际 GPL FFmpeg 许可/构建材料及 H3 官方许可副本已完成。后续恢复公开发行时仍需处理 FFmpeg 完整对应源码、Design/H3 原文与社区方法及其改编的适用授权，以及由账户持有人确认 Provider 商用/转售合同。修正 Notice 或附上许可文本本身不能替代这些授权与对应源码。

同日核查后已按授权修正 `NOTICES.md` 的 H3 来源和 LTX-2 标注，并加入 `licenses/FFmpeg-GPL-2.0.txt` 全文与 `licenses/FFmpeg-build.md`。包装负责人已在配置中排除未用 LTX-2 包和旧模型目录，并加入 `licenses/**/*`；实际候选包重建、空配置启动和导出由包装验收记录确认。本报告下面保留整改前发现，以说明改动原因；其中旧 `NOTICES.md` 行号对应 `output/commercial-delivery-2026-09-16/distribution-preflight/NOTICES.before.md`。FFmpeg 对应源码和方法/账户授权没有因此变成完成状态。

同日已从官方 Hugging Face 仓库取回 [H3 Community License 完整副本](../../licenses/MiniMax-H3-Community-License.txt)，并保存[来源、固定修订与取回日期说明](../../licenses/MiniMax-H3-source.md)。17,604 字节原文与官方固定修订 `42ed227ee7df40d41602854ae760620d6eb651fe` 逐字节一致；这两份文件已随 r5 携带并完成[包内核对](../../output/commercial-delivery-2026-09-16/native-final-r5-build.md)。此次仅补充公开许可材料，没有更改方法库或提交账户接受动作；不解决 Design、社区方法及其改编、Provider 账户授权，也不补齐 FFmpeg 对应源码。

## 1. 实际打包范围（整改前核查快照）

| 内容 | 当前证据 | 判断与最小动作 |
| --- | --- | --- |
| LTX Desktop 编辑代码、应用壳 | `LICENSE.txt` 保留 Apache-2.0 全文及 Lightricks 版权；`NOTICES.md:3` 声明复用；`electron-builder.yml:14` 将两者放入 ASAR。历史 `output/product-delivery/package/mac-arm64/Short Drama Studio.app` 的 ASAR 中也有这两个文件 | 继续保留许可证、适用版权/归属及修改说明。历史安装包的 Notice 早于当前源码，不能当成本轮重建已验收 |
| LTX 模型权重 | `electron-builder.yml:41` 排除 `backend/models`；当前 `python-embed` 未找到 `.safetensors`、`.ckpt`、`.pt` 模型权重文件 | 符合“不分发 LTX 权重”的产品边界；这不等于未分发 LTX 模型代码 |
| `ltx-core` / `ltx-pipelines` 推理代码 | `backend/pyproject.toml:64` 与 `uv.lock:507,614` 锁定 LTX-2 `9377758131b1ffde4b7f766804590a6617bf2ab9`；嵌入环境已安装两者 1.1.7；`electron-builder.yml:85` 整体复制 `python-embed` | 实际会随包。当前 `NOTICES.md:91`、`:92` 标注 Apache-2.0 缺乏本次锁定文件的依据，需纠正；推荐从短剧发行包排除未用代码，见下一节 |
| FFmpeg | macOS arm64 `imageio-ffmpeg` 0.6.0 带入 FFmpeg 7.1，`-L` 明确 GPL-2.0-or-later，启用 x264/x265 | 现有 imageio BSD 声明仅对应包装器；需要实际 FFmpeg 许可证及完整对应源码安排 |
| MiniMax Design 方法 | `skills/sources/README.md:7`、`:8` 和 manifests 明示来自本机 3.0.11 Workflow、3.0.14 Skill；`NOTICES.md:18` 已明确没有确认的公开再分发许可 | 当前打包仍复制全部 `.md/.txt/.yaml/.json`，包括原文和衍生章节。取得适用授权，或以可分发的自行编写材料替换，才可进入公开发行包 |
| MiniMax-H3 公开方法 | `methods/brand_promo/manifest.json:57` 等八个方法锁定 `MiniMax-H3@d21241f…`；现有 `NOTICES.md:10` 却链接另一个 `MiniMax-AI/skills` 仓库 | 修正来源，不能套用另一个仓库的 MIT 许可；实际 README 链接 H3 社区许可，见第 4 节 |
| ComfyUI 和 MiniMax Design 插件 | `studio_comfy.py:14,72` 是 HTTP 客户端；源码克隆在被排除的 `output` 目录，不在 Python 嵌入包；`NOTICES.md:22` 已说明单独运行 | 当前只分发适配层，不分发 ComfyUI/插件服务。无需因外部 HTTP 连接就把整个 Studio 改称 GPL；以后若随包分发插件，再对应保留 GPL、源码及修改信息。Design 工作流文本授权仍单独处理 |

LTX Desktop 的许可允许符合条件的商业再分发；要求提供许可、保留适用归属，并标明修改。该许可不自动覆盖另一个仓库的模型代码或第三方素材。[上游 Apache-2.0 文本](https://github.com/Lightricks/LTX-Desktop/blob/main/LICENSE.txt)

## 2. LTX-2：按锁定文件判断，优先排除未用推理部分

已核对安装的两份 `*.dist-info`：没有许可文件，元数据没有 `License` / `License-Expression`。锁定提交的两个包 `pyproject.toml` 也没有独立 Apache 声明；本次读取的包内源码没有许可头。锁定提交根许可是 LTX-2 Community License；定义明确覆盖机器学习模型代码、推理代码、配套源码和脚本，而不仅是权重。当前证据不能支持把这些实际文件统一标成 Apache-2.0。[锁定许可](https://github.com/Lightricks/LTX-2/blob/9377758131b1ffde4b7f766804590a6617bf2ab9/LICENSE)、[ltx-core 包定义](https://github.com/Lightricks/LTX-2/blob/9377758131b1ffde4b7f766804590a6617bf2ab9/packages/ltx-core/pyproject.toml)、[ltx-pipelines 包定义](https://github.com/Lightricks/LTX-2/blob/9377758131b1ffde4b7f766804590a6617bf2ab9/packages/ltx-pipelines/pyproject.toml)

实际短剧桌面从 `electron/python-backend.ts:303` 启动 `short_drama_server.py`。对 `short_drama.service.ShortDramaService`、音频语义服务类和 `_routes.short_drama.router` 做了本地导入检查：成功，路由数量 161，加载的 `ltx_core` / `ltx_pipelines` 模块数为 0。静态检查在 `backend/short_drama`、两个产品路由及 `short_drama_server.py` 中也未找到这两个包的导入。此次没有实例化项目服务或运行 HTTP 生命周期，不能替代移除后的原生回归。

现存引用集中在旧 LTX 入口和模型服务：

- `backend/ltx2_server.py:160`、`backend/handlers/pipelines_handler.py:417`。
- `backend/services/fast_video_pipeline/ltx_fast_video_pipeline.py:51`、`prompt_enhancer_pipeline/ltx_prompt_enhancer_pipeline.py:27`、`ic_lora_pipeline/ltx_ic_lora_pipeline.py:60`、`services/ltx_pipeline_common.py`，以及 a2v、text_encoder 和 patches 路径。
- 两处还明确说明复制/派生模型实现：`services/retake_pipeline/ltx_retake_pipeline.py:3` 来自 `ltx_pipelines.retake`；`services/patches/ic_lora_stage2_lora.py:33` 说明复制安装的 `ic_lora.py`，且标注的旧源码修订是 `a2c3f24`，不能假设与当前包修订相同。

最小包装修复建议：从短剧发行包排除 `lib/python*/site-packages/ltx_core/**`、`ltx_pipelines/**` 及对应 `*.dist-info/**`；同时排除未启用的旧 LTX 模型后端路径，至少包括上述两处明确的派生实现。不要为了排包删掉工作区源文件或升级依赖。若采用整个 `backend/services/**` / `handlers/**` / `ltx2_server.py` 的排除，须用正式短剧入口启动、SQLite 迁移和 FFmpeg 导出验证共享依赖没有遗漏；当前静态检查没有发现短剧入口导入这些目录。

保留 LTX Desktop 的 Apache 许可与编辑代码归属；若最终仍分发任一 LTX-2 模型源码，须核实该实际文件适用许可并携带完整许可与修改/归属材料，不能仅保留一个根仓库链接。上述排除已由包装负责人加入构建配置，实际候选包验收另记。

## 3. FFmpeg：已确认版本、来源链和随包缺项

实际二进制：

`python-embed/lib/python3.13/site-packages/imageio_ffmpeg/binaries/ffmpeg-macos-aarch64-v7.1`

- 本地 `-version` / `-L`：FFmpeg 7.1；Copyright 2000–2024 the FFmpeg developers；Apple clang 13.1.6；GPL version 2 or later。
- 构建含 `--enable-gpl --enable-libx264 --enable-libx265`；还含 libass、libfreetype、libharfbuzz、libopus、libmp3lame、libvpx、libaom 等。完整输出见本次 `distribution-preflight/ffmpeg-license.txt`。
- `backend/uv.lock:464` 锁定 wheel：`imageio_ffmpeg-0.6.0-py3-none-macosx_11_0_arm64.whl`，SHA-256 `b1ae3173414b5fc5f538a726c4e48ea97edc0d2cdc11f103afee655c463fa742`。这是锁文件中的 wheel 标识，没有在本轮重新下载 wheel。
- 上游 v0.6.0 `_definitions.py:50` 将 macOS arm64 映射到同名 FFmpeg 7.1；`tasks.py:115`、`:123` 从 `imageio/imageio-binaries/ffmpeg` 复制/下载该二进制。这确认分发来源链，但本次未找到这份二进制的完整对应源码和具体编译环境存档，不能把普通 FFmpeg 7.1 源码包当成已证明对应的源码。[版本映射](https://github.com/imageio/imageio-ffmpeg/blob/v0.6.0/imageio_ffmpeg/_definitions.py)、[wheel 构建脚本](https://github.com/imageio/imageio-ffmpeg/blob/v0.6.0/tasks.py)

当前已有文件：`python-embed/lib/python3.13/site-packages/imageio_ffmpeg-0.6.0.dist-info/LICENSE`，这是 imageio 包装器的 BSD-2-Clause 文本；`NOTICES.md:29` 只有一般性提示，`:96` 也只列包装器 BSD。当前包搜索没有找到实际 FFmpeg GPL 全文或对应源码说明。[包装器许可与打包定义](https://github.com/imageio/imageio-ffmpeg/blob/v0.6.0/setup.py)

FFmpeg 官方说明启用 GPL 部分会使 FFmpeg 适用 GPL；GPLv2 的二进制分发条件要求完整对应源码或有效的源码书面提供安排，源码包括相关模块及控制构建/安装的脚本。只加 `ffmpeg.org` 首页链接不足以说明这份静态编译二进制的对应源码。是否形成其他程序的派生作品需要看实际组合，不能据此直接断言整个 Studio 必须 GPL。[FFmpeg 官方许可说明](https://ffmpeg.org/legal.html)、[FFmpeg 7.1 随附 GPLv2 §2–3](https://github.com/FFmpeg/FFmpeg/blob/n7.1/COPYING.GPLv2)

恢复公开发行时的最小动作：保留包装器 BSD；增加实际 FFmpeg GPL 全文、版本/构建说明和完整对应源码交付方式。若无法取得当前构建的对应源码，换用有可核实来源与源码配套的构建，再做现有视频/音频导出回归。同日已将 GPL 全文和构建说明加入根 `licenses/`，更新 Notice 并明确对应源码交付未完成；没有创建未经兑现的源码承诺，也没有更换二进制。

r5 后续定位仍未取得精确对应材料。额外检查的 [OSXExperts 构建页面](https://www.osxexperts.net/) 实际源码链接指向 FFmpeg `release/6.1`，不能证明与当前 7.1 二进制对应，也没有据此认定它是本包构建者。一次针对 imageio 官方 issues/discussions 的搜索未找到符合版本的记录，这不证明上游没有材料。现已准备[源码与构建来源询问草稿](../../output/commercial-delivery-2026-09-16/distribution-preflight/ffmpeg-source-inquiry.md)，仅询问精确修订、外部库版本、脚本及原构建者；尚未对外发送。按最新范围，草稿暂缓、不发布，此前关于发布询问的许可问题已撤回。

## 4. 导入方法和可选 ComfyUI 的许可边界

Design 原文来源和作者元数据保存完整是有益的，但不等于已取得复制再分发权。`electron-builder.yml:28` 的规则会把 `sources/minimax_design`、`sources/design_library` 和对应 methods/Provider 衍生章节一起带入。仅排除 `sources/` 目录仍可能保留衍生正文。最小处理单位是 manifest 指明的来源及其实际被复制/改写内容；可以保留产品用途名、运行接口和独立编写的知识结构。

八项公开 H3 方法是 `brand_promo`、`co_op_game_intro`、`paper_collage_explainer`、`papercraft_stop_motion`、`minimalist_product_ad`、`lyric_video`、`animation_short`、`handdrawn_live`。其锁定仓库 [MiniMax-H3 README](https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/README.md) 第 448 行链接 H3 社区许可；不是现有 Notice 链接的 `MiniMax-AI/skills`。本地原文目录没有该许可文件；部分作者元数据还来自 Hub 社区作者，不能由仓库公开访问推导所有第三方内容均获无限制许可。

当前链接到的 H3 社区许可日期为 2026-08-02，定义覆盖公开 Documentation；有地域限制，商业界面名称要求和超过 2,000 万美元年收入时另授权的条件。若适用于这些分发材料，还要求许可副本、修改标记及 Notice。对经 AceData 获取的服务和输出，是否存在另行商业合同及如何传递适用条件仍须账户持有人确认，不能直接把开源模型许可当成该账户合同，也不能用“MiniMax 不主张输出权利”抹去其其他条件。[MiniMax H3 Community License](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE)

ComfyUI 和公共 MiniMax Design 插件各自保留 GPL-3.0；当前二者是用户单独配置的服务，Studio 只以 HTTP 上传素材、运行用户工作流并回收输出。应继续保留当前来源说明，不宣称随包提供节点、模型或 GPL 服务。工作流 JSON 是否能再分发取决于其来源，不能由 ComfyUI 引擎 GPL 推导 Design 私有模板授权。[ComfyUI License](https://github.com/Comfy-Org/ComfyUI/blob/master/LICENSE)、[MiniMax Design 公共插件](https://github.com/MiniMax-AI/minimax-desgin-plugin)

## 5. 当前 Provider 链路：公开规则与账户未知

配置事实来自同日脱敏预检 `output/commercial-delivery-2026-09-16/stage3-prep/provider-preflight.json`，只检查键名、是否非空及脱敏 host；没有读取余额、账户计划、合同或许可证明。主视频客户端 `providers/minimax_h3_client.py:151` 默认 `api.acedata.cloud`，不是 MiniMax 直连 API；图片为 `*.openai.azure.com`；语言为 `loopback:4141`。

| 链路 | 公开条款能支持什么 | 本轮不能确认什么 |
| --- | --- | --- |
| AceDataCloud H3 | 公开服务条款有 API/代理服务及书面合同优先规则，并明确限制用户转售、再分发或再许可其服务 | 当前账户是否有 OEM/转售协议、实际 MiniMax 下游商业条件、地域与终端用户范围。卖桌面应用、用户自行填写账号，与统一账户向他人销售生成服务是不同安排，不能混为同一许可结论 |
| Azure OpenAI 图片 | Microsoft Online Services 条款不主张客户输出所有权；输出属于 Customer Data。AI Code of Conduct 要求输入/发布内容有合法权利 | 当前订阅适用 MCA/EA 或其他合同、部署的具体服务条件、输入素材权利；输出不保证独有、可版权保护或不侵权。Copyright Commitment 是附带条件的保障，不能当作无条件商用背书 |
| 本地兼容代理语言 | 若实际上游是 OpenAI 商业 API，其 Services Agreement 允许把 API 集成进面向终端用户的应用，并规定双方之间的输出归属 | `loopback:4141` 与模型名称无法证明上游是 OpenAI、商业 API 账户，或有适当转供权。当前不确定的代理上游不能直接套用 OpenAI 条款；需由账户管理者确认服务商、账户类型和适用合同 |

主来源：[AceDataCloud 服务条款（页面标注 2025-03-16）](https://docs.acedata.cloud/en/resources/terms)、[Microsoft Online Services / MCA](https://www.microsoft.com/licensing/terms/product/ForOnlineServices/mca)、[Microsoft AI Code of Conduct](https://learn.microsoft.com/en-us/legal/ai-code-of-conduct)、[Azure Copyright Commitment 条件](https://learn.microsoft.com/en-us/azure/ai-foundry/responsible-ai/openai/customer-copyright-commitment?view=foundry-classic)、[OpenAI Services Agreement（2026-01-01 生效）](https://openai.com/policies/services-agreement/)。Microsoft MCA 页面只作为公开条款依据，不代表已经确认当前账户就是 MCA。

## 6. 后续公开发行清单（暂缓）

以下发行事项保留为未完成记录，待用户决定恢复发行后再推进；不作为本轮本地产品验收的阻碍，也不要求用户现在提供发行账户或授权材料。

同日 r5 验收后只读核实本机签名条件：`security find-identity -v -p codesigning` 返回 `0 valid identities found`；`notarytool` 已安装。当前无法直接使用有效开发者身份完成签名；这不等于账户持有人没有开发者账户，公证身份也未据此确认。实际输出与后续所需条件见[本机签名核对](../../output/commercial-delivery-2026-09-16/distribution-preflight/native-signing-availability.md)。未创建证书、接受协议或上传应用。

1. 构建过滤已加入未启用 LTX-2 包与旧模型代码排除；保留 LTX Desktop 编辑许可。重建后检查 ASAR/Resources 清单、正式后端启动、空库迁移、视频和音频导出，不运行旧任务。
2. H3 来源链接及不成立的 Apache 标注已修正；对 Design/H3 原文及衍生方法取得适用分发授权或完成可分发替换仍待处理。保留真实来源，不以改名或删除出处代替授权。
3. FFmpeg 实际 GPL 全文与构建说明已加入 `licenses/`，对应源码交付安排未完成。最初草稿和只读输出保存在 `output/commercial-delivery-2026-09-16/distribution-preflight/`；不能将其当作公开发行完成。
4. 如恢复公开发行，由账户持有人确认 AceData 的服务转售/终端用户条件、H3 适用商业安排、Azure 实际订阅合同和语言代理上游；这些事实无法由改代码补齐。本轮不接受任何条款，不联系外部人员，也不宣称具备完整法律合规。

恢复公开发行并完成上述事项后，仍应对“实际准备发布的那个包”核对内容，而非使用旧安装包、仓库标签或开发环境依赖列表代替。
