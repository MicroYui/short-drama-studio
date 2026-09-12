# H3 明确时间区间的等价识别与单镜输入补全

## 实际问题

2026-09-12 08:59:13，项目 `project-5b8286dc63e6` 的工单 `work-order-5f47ef21087143c69d54f76fb53eb3fa` 第 6 个 Tool 回合调用 `route.compile_h3_prompt`，被本地校验拒绝：`omitted locked timeline range [0.00-4.50 seconds]`。实际编译模型为 `model-e2748fa827a3`，原输入文件为 `20260912T005809867057-9000cbea-model.input.json`。

当前 `shot-yqq-08` revision 2 锁定两个 beat：0–4.5 秒完整说一次 line-yqq-06，4.5–5 秒闭口等待。被拒稿的 `detailed_description` 已按顺序写明 `00:00.000–00:04.500` 和 `00:04.500–00:05.000`，完整对白也在第一段。原校验使用确定性编译器的方括号、连字符和两位小数字符串逐字查找，因而把数值相同的区间当成遗漏。只读重放中，仅把这两处改为原方括号秒数格式，其余全文不改，即通过全部原校验；根因是表示法误拒。

09:07:22 同工单第 12 回合是另一种情况：`shot-yqq-12-reaction` 锁定 0–1.5 秒一个 beat，草稿整体区间只在 summary、subject 或 retention 中出现；正文写的是 0–约 1.2 秒、约 1.2–1.5 秒，没有整体区间。此次仍应拒绝，不能用正文外的提及代替正文，也不将近似子区间推断成完整锁定区间。该稿与第 6 回合一起保留为回归数据。

两次实际单镜语言输入都只有 `locked_shot.timeline_beats` 的数值字段，没有明确的 `required_timing_marker`。组编译已有窗口标记，单镜输入却让模型自行猜本地校验的字面格式，增加了无内容收益的返工。

## 公开方法与采纳边界

MiniMax 官方 [Ref2VA 方法](https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/skills/h3-prompt-writing/references/ref-en.txt) 使用分钟、秒和毫秒表示切镜时刻；[基础模式方法](https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/skills/h3-prompt-writing/references/base-en.txt) 同样用递增的时刻标记后续切镜。官方正文按播放顺序组织视听动作，完整对白留在正文；首尾帧对齐又有自己的特定格式。此次沿用这些时间和正文职责，不把本地 `[start-end seconds]` beat 标记说成官方所有模式的唯一语法，也不改变首尾帧对齐行。

修复只解释明确写出的完整区间：秒数（尾端有 seconds/sec/s/秒单位）或两端都是 `MM:SS.mmm` / `HH:MM:SS.mmm` 时间码，区间连接符允许 `-`、`–`、`—`。按数值匹配，返回原文位置，保留 authored prompt 原文。数字边界防止从较长数字、非法时码或指数表达中截出匹配片段；时间码的非首段不得达到 60。混合时码与无单位数字、单独数量范围、近似子段求并集均不解释。

边界比较保留原编译器两位小数精度的兼容，例如来源为 1/3 秒时，既有 0.33 秒标记仍有效；也允许精确锁定值。除此之外只容忍浮点计算误差，不按“看起来接近”放宽区间。对实际 4.500 秒改成 4.501 秒的稿件仍拒绝。

## 实现

- `providers/h3_timing.py` 提供共享格式化与有边界的等价区间查找。`h3_prompt_skill.py` 单镜 beat 与组窗口复用它；仍按真实区间在正文出现的位置验证顺序、对白归属、完整性和重复。
- 组窗口必须跟在本窗口对应的 `[Shot N]` 后，并只在已声明正文到 `overall_soundscape` 之间查找。参考说明可以正常提及同一镜号与时段，不会抢先成为正文窗口。组历史稿未列每个 beat 的已有兼容策略保留：不新增每 beat 强制标记要求；已明确给齐 beat 时仍检查对白所处区间。
- `build_language_prompt_input` 的单镜 beat 增加 `required_timing_marker`，其余原数值、动作、对白 ID 不变；同一输入说明要在正文按顺序使用完整标记或数值完全等价的时码。没有新增 Provider 请求、Tool 参数或持久状态。
- `tests/fixtures/h3_time_range_actual.json` 来自实际 t6/t12 的参数、Shot 和被拒全文。仅将四个媒体本机地址改为 `/fixtures/<version>.png`；被拒 prompt 正文保持原样。校验器不读图片，无需保留本机输出路径。原运行 Trace、项目与素材均未改。

## 验证与限制

新专测覆盖实际 t6 的原样通过、秒数和时码等价、起止边界改变、缺失、重排、对白移位/重复、正文外范围，以及实际 t12 的拒绝。组用例覆盖等价窗口、原历史 beat 兼容、真正变更窗口/对白移位、参考说明提及时段与正文窗口独立。单镜 payload 用两份实际 Shot 对比，去掉新增标记后原 beat 字段逐值不变。

独立 worktree 的 7 文件联合回归为 **165 passed / 8.95s**，其中新时间区间专测 28 项；相关 5 个源/测试文件 Pyright 为 **0 errors / 0 warnings**。复用主仓库已有虚拟环境，没有安装依赖。验证命令为：

```sh
<main-backend>/.venv/bin/python -m pytest tests/test_h3_time_ranges.py tests/test_h3_dialogue_continuation.py tests/test_h3_cross_shot_dialogue.py tests/test_h3_generation_unit_prompt.py tests/test_h3_generation_unit_language.py tests/test_mature_skill_compilation.py tests/test_language_provider.py -q
<main-backend>/.venv/bin/pyright --venvpath <main-backend> short_drama/providers/h3_timing.py short_drama/providers/h3_prompt_skill.py short_drama/providers/h3_language_prompt.py tests/test_h3_time_ranges.py tests/test_language_provider.py
```

另一位 Agent 对最终源码和 28 个新用例完成只读复核，未发现具体阻断；该复核没有重复执行测试或调用 Provider。`git diff --cached --check` 通过。

此次是离线合同修复，未调用真实语言或视频 Provider，未证明新一轮 Agent 已减少某个具体耗时；H3 提交、legacy 路由写回与前置准备交接不在本次修改范围。
