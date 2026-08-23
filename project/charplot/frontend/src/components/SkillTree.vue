<script setup lang="ts">
// 技能树图渲染封装 (Issue 04): vue-flow + dagre 布局.
// 选型: vue-flow (Vue 3 原生, 节点即 Vue 组件, 点亮动效自由定制) + dagre
// (DAG 分层布局, 前置在上 / 依赖在下), 理由与对比见 docs/adr/0004.
// 点击事件: 锁定节点忽略, 其余 emit select 交地图页跳转关卡入口.
import { computed } from 'vue'
import { Background } from '@vue-flow/background'
import {
  VueFlow,
  type Edge,
  type Node,
  type NodeMouseEvent,
} from '@vue-flow/core'
import dagre from 'dagre'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import type { SkillTreeData, SkillTreeEdge, SkillTreeNode } from '@/api/client'
import SkillNode from './SkillNode.vue'

const props = defineProps<{ tree: SkillTreeData }>()
const emit = defineEmits<{ select: [nodeId: number] }>()

const NODE_W = 210
const NODE_H = 90

/** dagre DAG 分层布局: 知识结构自上而下 (rankdir=TB). */
function dagreLayout(nodes: SkillTreeNode[], edges: SkillTreeEdge[]) {
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: 'TB', nodesep: 40, ranksep: 72 })
  g.setDefaultEdgeLabel(() => ({}))
  for (const n of nodes) g.setNode(String(n.id), { width: NODE_W, height: NODE_H })
  for (const e of edges) g.setEdge(String(e.source), String(e.target))
  dagre.layout(g)
  return g
}

/** 节点映射: 挂章节/状态数据, 位置由 dagre 计算 (fitView 兜底缩放). */
const flowNodes = computed<Node[]>(() => {
  const g = dagreLayout(props.tree.nodes, props.tree.edges)
  return props.tree.nodes.map((n) => {
    const pos = g.node(String(n.id))
    return {
      id: String(n.id),
      type: 'skill',
      position: { x: pos.x - NODE_W / 2, y: pos.y - NODE_H / 2 },
      data: { ...n },
    }
  })
})

/** 依赖边: smoothstep 直角折线适合分层 DAG, 柔和粉色弱化 (点亮是主角). */
const flowEdges = computed<Edge[]>(() =>
  props.tree.edges.map((e) => ({
    id: e.id,
    source: String(e.source),
    target: String(e.target),
    type: 'smoothstep',
    style: { stroke: '#e8c4d3', strokeWidth: 2 },
  })),
)

function onNodeClick(event: NodeMouseEvent) {
  const status = (event.node.data as SkillTreeNode).status
  if (status !== 'locked') emit('select', Number(event.node.id))
}
</script>

<template>
  <VueFlow
    :nodes="flowNodes"
    :edges="flowEdges"
    :nodes-draggable="false"
    :nodes-connectable="false"
    :min-zoom="0.15"
    :max-zoom="2"
    :fit-view-options="{ padding: 0.25 }"
    fit-view-on-init
    class="skill-flow"
    @node-click="onNodeClick"
  >
    <template #node-skill="slotProps">
      <SkillNode v-bind="slotProps" />
    </template>
    <!-- 浅色点阵: 地图氛围, 与主题背景渐变相容 -->
    <Background :gap="26" :size="1.6" pattern-color="#f3cfdc" />
  </VueFlow>
</template>

<style scoped>
.skill-flow {
  width: 100%;
  height: 100%;
  background:
    radial-gradient(circle at 12% 18%, rgba(251, 114, 153, 0.07), transparent 42%),
    radial-gradient(circle at 88% 82%, rgba(165, 207, 227, 0.1), transparent 46%),
    linear-gradient(160deg, #fffbfd, #fdf4f8 55%, #f2f8fc);
}

/* 锁定节点的边随源节点灰化: 依赖边整体弱化, 不抢点亮主角 */
.skill-flow :deep(.vue-flow__edge-path) {
  transition: stroke 0.3s ease;
}
</style>
