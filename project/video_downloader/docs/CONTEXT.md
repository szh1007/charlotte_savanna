# CONTEXT.md — BilibiliDownloader (video_downloader)

> 领域术语表（Ubiquitous Language）。只记录术语定义，不包含实现细节。

## 核心术语

| 术语 | 定义 |
|------|------|
| **下载任务 (DownloadTask)** | 一次视频下载的完整单元：一个来源链接 + 一个选定格式档位 + 一份状态。任务生命周期由状态机驱动。 |
| **解析 (Resolve)** | 从来源链接提取视频元信息（标题、封面、时长、可用格式档位列表）的过程。解析成功后用户才能选档并发起下载。 |
| **格式档位 (Format)** | 某一清晰度与容器格式的组合（如 `720p MP4`、`1080p MP4`、`最佳画质`）。档位是付费差异的核心载体。 |
| **来源链接 (Source URL)** | 用户在页面粘贴的哔哩哔哩视频页面链接（bilibili.com 主域/子域或 b23.tv 短链, 见 ADR-0004）。 |
| **交付链接 (Delivery Link)** | 下载完成后生成的临时直链，供浏览器保存视频文件；具有有效期，过期即失效并清理文件。 |
| **批量队列 (Batch Queue)** | 多个下载任务排队依次执行的机制。免费与会员档的队列长度上限不同。 |
| **会员 (Member)** | 通过会员密钥验证获得完整能力（全部清晰度、更高并发、更长队列）的使用者身份。 |
| **支持平台 (Supported Site)** | 当前唯一支持的站点：哔哩哔哩（域名白名单校验, 见 ADR-0004）。平台列表接口仅返回哔哩哔哩。 |
| **下载进度流 (Progress Stream)** | 后端向前端单向推送的任务进度事件流（解析中 / 下载中百分比 / 完成 / 失败）。 |
| **总结任务 (SummaryTask)** | kind=`summary` 的任务：一次来源链接 + 一份转录文本 + 一份总结结果。与下载任务共享状态机与进度流（ADR-0005）。 |
| **转录文本 (Transcript)** | 带时间戳的视频文字内容，总结与问答的共同原料。来源二选一：字幕快路径（服务端 cookie 取官方字幕, 秒级）或 SenseVoice 转写（兜底, 全覆盖）。 |
| **视频总结 (Video Summary)** | LLM 从转录文本生成的结构化输出：章节时间线 + 要点大纲（JSON）。思维导图与前端展示均由同一份结构化数据渲染。 |
| **思维导图 (Mind Map)** | 从视频总结的结构化数据渲染的树形图，与总结同源、无独立生成过程。 |
| **AI 问答 (Q&A)** | 针对视频内容的对话：上下文 = 该视频的转录文本 + 视频总结（单次塞入 LLM, 不建向量库）。 |
| **每日配额 (Daily Quota)** | 免费档按匿名身份计数的每日使用上限（总结 3 次 / 问答 10 次），会员不限；内存态计数, 服务重启清零。 |

## 任务状态机

```
下载任务 (kind=download):
pending → resolving → resolved → queued → downloading → completed
                              ↘                ↘
                               failed            failed
completed → expired（交付链接过期，文件被清理；failed 无交付资产，保持终态）

总结任务 (kind=summary):
pending → queued → transcribing → summarizing → completed
                ↘                ↘
                 failed            failed
completed → expired（转录与总结随 TTL 清理, 用户可导出永久保存）
```

- 下载任务：`pending` 新建待解析；`resolved` 已解析等待入队；`queued` 排队中；`downloading` 执行中；`completed` 文件已就绪；`failed` 出错（含超时、引擎拒绝）；`expired` 交付链接过期。
- 总结任务：`pending` 内同步做轻量元信息解析（不经过 `resolving`, 失败不阻塞总结）；`queued` 排队中（派发时进入）；`transcribing` 获取转录文本（字幕提取或 ASR 转写, 进度可见）；`summarizing` LLM 生成总结；`completed` 转录与总结就绪。

## 边界与约束（领域级）

- **只下载不破解**：不绕过 DRM 加密、不破解付费内容。引擎能力即为领域能力边界。
- **交付有期限**：任何下载文件都不是永久资产，默认 24h 后失效清理；转录与总结结果同为短生命周期资产, 用户可通过导出永久保存。
- **免费档真实受限**：免费与会员的能力差异是产品设计的一部分，不是 UI 摆设（含每日配额）。
- **不收集用户凭据**：字幕快路径使用服务端自备 cookie, 不收集、不使用任何用户提供的 cookie 或登录态（ADR-0005）。
