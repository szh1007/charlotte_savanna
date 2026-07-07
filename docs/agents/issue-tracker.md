# Issue tracker: Local Markdown

Issues 和 PRD 以 markdown 文件形式保存在 `.scratch/` 目录下，不自动同步到 GitHub。用户自行决定何时提交推送到远程。

## Conventions

- 每个功能一个目录：`.scratch/<feature-slug>/`
- PRD 文件：`.scratch/<feature-slug>/PRD.md`
- 实现 issue：`.scratch/<feature-slug>/issues/<NN>-<slug>.md`，从 `01` 开始编号
- Triage 状态记录在每个 issue 文件顶部附近的 `Status:` 行（状态值参见 `triage-labels.md`）
- 评论和对话历史追加到文件底部的 `## Comments` 标题下

## When a skill says "publish to the issue tracker"

在 `.scratch/<feature-slug>/` 下创建新文件（目录不存在时先创建）。

## When a skill says "fetch the relevant ticket"

读取指定路径的文件。用户通常会直接传入路径或 issue 编号。

## Wayfinding operations

由 `/wayfinder` 使用。**map** 是一个文件，每个 **child** 对应一个 ticket 文件。

- **Map**: `.scratch/<effort>/map.md` — 包含 Notes / Decisions-so-far / Fog 内容。
- **Child ticket**: `.scratch/<effort>/issues/NN-<slug>.md`，从 `01` 编号，body 中写问题。`Type:` 行记录 ticket 类型（`research`/`prototype`/`grilling`/`task`）；`Status:` 行记录 `claimed`/`resolved`。
- **Blocking**: 文件顶部附近的 `Blocked by: NN, NN` 行。当所列文件全部 `resolved` 时，ticket 解除阻塞。
- **Frontier**: 扫描 `.scratch/<effort>/issues/` 中 open、unblocked、unclaimed 的文件；按编号优先。
- **Claim**: 设置 `Status: claimed` 并保存，在任何工作开始之前。
- **Resolve**: 在 `## Answer` 标题下追加答案，设置 `Status: resolved`，然后将上下文指针（gist + link）追加到 `map.md` 的 Decisions-so-far 中。
