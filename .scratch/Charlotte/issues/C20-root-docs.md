# C20 · 根 `README.md` + 根 `CLAUDE.md` 更新

**Status:** todo

**Type:** docs

**Blocked by:** C19

**上游:** `.scratch/Charlotte/PLAN.md` §7；`CharApp/docs/PLAN.md:194-197`（当初押后的理由与解除条件）

## 为什么这一票现在才做

`CharApp/docs/PLAN.md:194-197` 记着一条决定：

> 更新根 `CLAUDE.md` 与 `README.md` → **已决意压后**：2026-09-18 的指示是「两个模块完全实现之前不要动根文档」，2026-09-21 复核为「**整个项目正式完成之后**才同步」。

**2026-09-30 解除这条** —— 理由：**门户本身是交付物**，不是收尾动作。一个公开仓库的第一屏看不见最好的作品，等于那些工作没做。

---

## 一、根 `README.md`

**现状**：写的是「个人技术学习项目」，模块表里只有 `app/minimall/` 等 —— **grep `CharAgent|CharApp` 零命中**。

改动：

1. **项目简介改写**：从「学习项目」改成能反映实际产出的描述（四条经历的存在本身改变了这个仓库的性质）
2. **模块表加两个顶层目录**：`CharAgent/`（从 0 手写的 agent 运行时）与 `CharApp/`（电商智能客服）
3. **新增一节「作品集导览」**：四条经历各一行 + 指向各自 README —— 这是面试官的第一屏，也是 D3 里「把 RAG 两个项目串成一条」的落点（具体措辞见 C19 末节）
4. **`demo/` 标明为学习区**：它不属于业务代码（根 CLAUDE.md §1.1 已经这么说，README 要跟上）—— 1119 个跟踪文件里相当一部分是教程，**不标注会让仓库显得像杂物间**
5. 技术栈徽章补充或修正（现在没有 Milvus / 向量库的位置）

## 二、根 `CLAUDE.md`

改动：

1. **§3 项目结构**：加 `CharAgent/` 与 `CharApp/` 两个顶层目录及其内部结构
2. **§4 框架特定规范**：**新增一节讲 `CharAgent` 的约定**（依赖方向 `CharApp → CharAgent` 单向、框架对业务零知识、加能力不许动 `agent/loop.py`、`pyproject.toml` 的依赖清单就是「零框架依赖」的证据）
3. **§4.8 的表述修正**（**C03 留下的指针**）：现在写着「DeepAgents = 检索/解构 subagent」——「解构」也不是 subagent（`pipeline/stages/deconstruct.py` 是裸 LLM 调用），同 C03 的口径一起改
4. **§5 当前开发状态**：加 `CharAgent` / `CharApp` 的状态表；`project/charplot/` 那节按 C03 的结果校正
5. **§6.3 开发约定**：加 `CharAgent` / `CharApp` 的启动方式（含 C04 的 `sh/charapp_demo.sh`）与 `.env` 变量段（`CHARAPP_*`）
6. **§6.6 文档同步维护**：那条「每周六 21:00 主动询问」的约定在收尾阶段可以保留，但**「最后更新」字段要刷新**

## 三、顺带

- `requirements.txt`：本阶段新增的依赖（MCP SDK **已经在里面了**，不需要动；`langchain-mcp-adapters` 若最终没用上可以去掉）
- `.env.example`：补 `CHARAPP_MILVUS_URL` / `CHARAPP_MODELSCOPE_ROOT` 等 C08 新增的键

---

## 验收

- [ ] 根 README 的第一屏能看出四条经历是什么，且每条都点得进对应的 README
- [ ] 根 README 里 `CharAgent` / `CharApp` 有命中（这条是这次改动的**最低验收线**）
- [ ] `demo/` 被明确标注为学习区
- [ ] 根 CLAUDE.md 的目录树、状态表、启动命令、`CHARAPP_*` 段都补齐
- [ ] **§4.8 的「解构 subagent」表述改掉**
- [ ] `.env.example` 与代码里实际读的键**逐条对齐**（有现成的做法可参考：三个子项目的 `.env.example` 都是全的）
- [ ] 「最后更新」字段刷新

## 改了哪些文件

（实施时补）

## 实施记录

（实施时补）
