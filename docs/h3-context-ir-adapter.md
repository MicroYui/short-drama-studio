# 可选官方 H3-Context-IR 编译

默认路线仍是 `LANGUAGE`。`route.prepare_h3_group` 可以明确选择 `compile_mode: "H3_CONTEXT_IR"`，直接把当前生成组的正式事实和按序媒体交给官方编译器，再使用同一套 H3 Prompt IR、时序、对白及引用校验。两条编译路线互斥，不先经 Astra 扩写，也不在官方结果后再追加语言扩写。

上游经本地 Skill 指导形成的正式内容会保留；官方编译器使用自身内部提示词系统，本地语言编译器的 Skill 原文不会整包注入。返回的方法 ID 记录源内容的选择，不代表官方执行了同一份本地提示词。

```json
{
  "project_id": "当前项目 ID",
  "request": {
    "shot_id": "当前组内任一镜头 ID",
    "compile_mode": "H3_CONTEXT_IR",
    "enqueue": false
  }
}
```

此选项会提交官方提示词任务；`enqueue` 只控制之后的视频生成。需要单独设置 `MINIMAX_OFFICIAL_API_KEY`，不能把当前 AceData 视频 token 当作官方 MiniMax 凭据。未配置时返回具体错误，不更换默认 Provider。

官方接口是 `POST https://api.minimax.io/v2/h3_context_ir`，使用 Bearer 鉴权；结果从 `GET /v2/query/video_generation/{task_id}` 的 `content.prompt` 读取。任务不是视频。公开查询和列表只覆盖最近七天。[创建接口](https://platform.minimax.io/docs/api-reference/video-generation-v2-h3-context-ir)、[查询接口](https://platform.minimax.io/docs/api-reference/video-generation-v2-query)、[列表接口](https://platform.minimax.io/docs/api-reference/video-generation-v2-list)。

现有 AceData 的 `/minimax/videos`、`POST /minimax/tasks` 及账户凭据不兼容这个官方入口。适配器复用本项目已有的 H3 内容/任务/用量模型、模式与输入校验，以及本地媒体 Data URI 编码。官方支持公开 URL、Data URI 与 `mm_file://`；来源不明的 AceData `file_id` 不会自动改成官方文件。[输入格式与限制](https://platform.minimax.io/docs/api-reference/video-generation-v2-h3-context-ir)。

官方每条输入文本同样限制 7,000 字符，总请求限制 64 MB。当前适配器仅确定性去掉源数据的空字段，保留逐镜事实、原台词、时间标记、项目方向和媒体顺序；超限在提交前报告实际字符数，不截断内容。采用现有组输入范围，当前产品仍只支持 9:16；官方接口支持其他画幅不等于本产品链已经支持。[官方约束](https://platform.minimax.io/docs/api-reference/video-generation-v2-h3-context-ir)。

每次操作只提交或查询一次。`WAITING_PROMPT` 返回同一 `operation_id`、官方任务 ID 和下次查询时间；原操作恢复时不会重新提交。成功原文先保存到工作区 `h3-context-ir` 缓存，再做本地契约校验；即使输出未通过校验也不会自动再收费扩写。已完成且未变的输入可以在重启后直接使用缓存，不依赖密钥仍在环境中。

组合 Tool 的等待回执在顶层提供 `operation_id`、`provider_prompt_cache_id`、`provider_prompt_task_id`、UTC ISO 8601 格式的 `next_poll_at` 和 `enqueue_requested`，状态为 `WAITING_PROMPT`、阶段为 `PROMPT_COMPILATION`，没有错误。Runner 据保存的时间恢复 `route.prepare_h3_group`，参数为原 `project_id` 和 `request: {operation_id, enqueue: enqueue_requested}`，不经过语言轮询。15 秒查询窗口内的重复恢复直接返回已保存状态；完成后回到同一生成组 Route 和任务交接。[完整组操作](h3-group-operation.md)。

提交前保存状态；网络中断且无法确认是否创建任务时显示 `PROMPT_SUBMIT_UNKNOWN` 并保留缓存 ID，禁止盲目重投。当前没有可靠的公开请求指纹回显可以自动匹配未知任务，需先在官方任务记录中确认；不能把这种情况报告成“未提交”。已知失败的显式重试保留既有成功步骤。

2026-09-11 验收为 Fake HTTP 与本地正式组数据：独立地址/鉴权、按序输入、恢复、15 秒查询间隔、空结果、错误归类、成功缓存、原 H3 校验和无第二语言调用。组合入口另外验证了等待回执、重启后恢复并进入原 H3 任务、重复恢复不重复提交，以及未知提示词提交保留缓存 ID。未调用真实收费 Context-IR，未确认当前账户权限，也未证明画面质量或耗时优于默认编译。默认使用哪条路线仍需相同组、媒体和交付契约的窄范围对照后决定。
