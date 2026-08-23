# 技能树图渲染库选型：vue-flow + dagre

闯关地图页（Issue 04, PRD D-1）需要把知识图谱渲染为技能树：知识点节点 + 前置依赖边（DAG），节点带点亮状态与多关进度徽章，点亮动效为产品亮点。候选：relation-graph / vue-flow / AntV X6。

**Status**: accepted

**Context**: 前端栈为 Vue 3 + Vite + TypeScript。核心需求是**强定制节点**（状态三色、进度徽章、点亮动画、点击跳转）与 DAG 分层布局，而非复杂流程编辑。

**Decision**: 采用 `@vue-flow/core` + `dagre` 组合。

- 节点 = Vue 组件（slot `#node-*` + `NodeProps<T>`）：状态样式 / 点亮动效 / 徽章 / 点击事件全部以 Vue 能力实现，无库侧定制成本
- dagre 提供 DAG 分层布局（rankdir=TB, 前置在上 / 依赖在下），vue-flow 官方推荐的布局方案（库本身无内置布局，刻意插件化）
- 配套 `@vue-flow/background` 渲染浅色点阵画布，营造地图氛围

**Considered Options**:

- relation-graph：开箱即用（自带布局/工具栏），但节点定制自由度低，技能树节点需要强定制（状态/动效/徽章），且文档与维护活跃度一般
- AntV X6：功能最强（bpmn 级流程引擎），但体积 ~1MB，Vue 3 集成需额外封装 `@antv/x6-vue-shape`，对本项目的「展示型技能树」场景属于杀鸡用牛刀
- vue-flow + dagre（采纳）：~40KB 轻量、Vue 3 原生、TypeScript 类型友好（`NodeProps<T>` 泛型透传）、节点即组件定制自由；dagre 布局输出确定性位置，配合 `fitView` 缩放兜底

**Consequences**: dagre 布局为静态计算（数据变更重建），不支持交互式拖拽重排 —— 地图页为只读展示场景，可接受。Issue 05 关卡系统接入后节点点击跳转链路不变（SkillTree 组件 `select` 事件已与路由解耦）。若未来需要自由画布编辑（如 Boss 战流程设计器），需重新评估 X6。
