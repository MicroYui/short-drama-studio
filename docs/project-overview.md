# AI 抖音短剧工作流调研

本仓库已经以 LTX Desktop `v1.1.0` 建立 Phase 0 上游工程基线，完成 Phase 1 供应商中立 Mock 短剧闭环，并完成 Phase 2 的 MiniMax-H3 768P Provider 工程接入与真实合同冒烟。首条 Direct 样本已完成创建、下载和人工审片，声音无明显问题，但视觉因店招乱码和录音笔变成对讲机被判硬门槛失败；因此工程闭环成立，最终真人质量仍未通过。

## 结论先行

高质量 AI 短剧不应被建模为“一句话直接生成一个长视频”，而应是一条可回溯的制片流水线：

`创意/剧本 → 剧本与风格约束 → 角色/场景/道具资产 → 分镜与连续性计划 → 单镜头多候选生成 → 质量复核与定向重试 → 时间线合成 → 字幕/声音/成片验收`

GPT Image 2、MiniMax-H3 和 Azure Speech 是**可用能力池，不是形式上的必选清单**。系统不以“用上哪些模型”为成功标准，而以最终成片质量、跨镜连续性和可控性为标准。某项能力只要能提高质量，或至少不降低质量并提高一致性、可控性或稳定性，就可以进入默认链路；不要求先看到失败才启用。成本只做运营记录，不主导路线选择。

首版按**单用户、受信环境**设计，不做注册、登录、账号、角色权限、租户、团队协作和分享权限。内容确认仍然保留，但它表示剧本、资产、分镜或候选是否获准进入下一生产阶段，不是用户权限审批。模型供应商 API Key 仍需作为运行配置保存，因为它属于外部调用凭证，而不是产品登录系统。

即梦、可灵、LTX Studio 和纳逗 Pro 仅作为产品与制作方法的研究对象，不是可用执行器，最终方案不得依赖其产品、账号或项目文件。当前视频生成只接 MiniMax-H3，并验证其 Direct、Frame、Reference、原生人声、对白、音效与口型；Azure Speech 可在不降低音画质量且能增强文本、声线或时间控制时加入。GPT Image 2 若能让角色、场景、构图或镜头衔接更强，或质量持平但一致性更稳定，就直接使用；只有实测造成风格损失、表演受限或下游视频更差时才跳过。

开源项目不建议原样照搬：

- LTX Desktop 已确定为首选代码底座与未来 fork 来源，重点保留桌面应用壳、生成/多 Take/Retake/Gap Fill、短剧生产时间线、模型管理框架与 FFmpeg 导出；
- Jellyfish 只参考项目、资产、分镜准备度和持久化任务语义，并作为 LTX 结构性失败时的回退；
- Wind Comic 最适合参考竖屏成片、字幕、FFmpeg 合成、质量回路和成本记录；
- LumenX 只参考模型目录、生成工作台和候选管理，不进入底座实现；
- Toonflow 最适合复用短剧编剧与分镜 Skill；
- ViMax 最适合复用首尾帧拆解和机位连续性思路；
- ComfyUI 适合作为后期修复/本地模型的可选侧车，不适合作为项目主控层。

首版已确认采用“LTX Desktop 壳 + 自建短剧/任务核心”的本地桌面方案；Jellyfish 只作为领域/任务语义参考及结构性失败回退。LTX 模型权重不进入当前产品，视频生成固定只接 H3；H3 内部媒体执行路线仍由固定输入质量实验决定。

## 文档导航

1. [调研总览与证据规则](README.md)
2. [目标、方法与成功标准](01-method-and-success-criteria.md)
3. [可用能力池与供应商基线](02-provider-capability-baseline.md)
4. [闭源平台拆解](03-closed-platforms.md)
5. [开源源码审查与复用建议](04-open-source-code-review.md)
6. [固定输入实验与验收方案](05-experiment-plan.md)
7. [当前架构与技术设计](06-candidate-architecture-hypotheses.md)
8. [当前结论与待讨论决策](07-decisions-and-open-questions.md)
9. [逐项确认决策账本](08-decision-register.md)
10. [来源与源码快照](sources.md)
11. [Phase 0：LTX Desktop 上游基线](09-phase0-upstream-baseline.md)
12. [Phase 1：供应商中立 Mock 垂直切片](10-phase1-mock-vertical-slice.md)
13. [Phase 2：MiniMax-H3 768P Provider 与真实证据门](11-phase2-minimax-h3-provider.md)

## 当前边界

- 已做：官方资料与开源源码审查、固定样片、架构和实验协议；LTX Desktop 基线；SQLite/API 短剧领域核心、可恢复 Mock Worker、多 Take、局部返工、TimelineManifest、字幕/音频占位、9:16 导出、QA 和桌面闭环；MiniMax-H3 768P Adapter、能力校验、持久化状态、对账/恢复、费用记录、离线合约、桌面实验台，以及一条已人工审片的真实 Direct 合同冒烟。
- 不做：把闭源平台接入或作为生产执行器；不做注册、登录、RBAC、多租户、团队权限、抖音账号接入、自动发布和运营管理。尚未做：GPT Image 正式资产生产、固定资产下的 H3 多路线质量实验、锁定对白/声线/口型实验和真人整集质量验收。
- 下一步：建立资产工作台与 GPT Image 生成/编辑闭环，为镜头 1/2/5/8 准备批准的角色、场景和道具版本，再运行 Direct、Frame、Reference 固定输入实验。
