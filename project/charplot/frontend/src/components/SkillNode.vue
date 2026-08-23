<script setup lang="ts">
// 技能树节点 (Issue 04): 状态样式 + 多关进度徽章.
// 点亮动效 (is-cleared 光晕 + 点亮动画) 是闯关地图的签名元素 (PRD 亮点 1),
// 动效集中在此一处, 画布其余保持安静. 锁定/可挑战/已通关文案见地图页图例.
// 点击跳转由 SkillTree.vue 统一处理 (node-click), 节点组件不感知路由.
import { computed } from 'vue'
import { Handle, Position, type NodeProps } from '@vue-flow/core'
import type { SkillTreeNode } from '@/api/client'

const props = defineProps<NodeProps<SkillTreeNode>>()

/** 多关进度徽章文本: 有 Level 数据才显示 (如 2/3), 本期恒为空. */
const progressText = computed(() => {
  const { cleared_levels, total_levels } = props.data
  return total_levels > 0 ? `${cleared_levels}/${total_levels}` : ''
})
</script>

<template>
  <div
    class="skill-node"
    :class="`is-${data.status}`"
    :title="`${data.chapter_title} · ${data.title}`"
  >
    <!-- 前置依赖入口 (上方) / 依赖出口 (下方), 边在此挂接 -->
    <Handle type="target" :position="Position.Top" />
    <div class="node-inner">
      <span class="chapter-tag">{{ data.chapter_title }}</span>
      <h3 class="node-title">{{ data.title }}</h3>
      <span v-if="progressText" class="progress-badge">{{ progressText }}</span>
      <span v-else-if="data.status === 'locked'" class="lock-mark" aria-hidden="true">🔒</span>
    </div>
    <Handle type="source" :position="Position.Bottom" />
  </div>
</template>

<style scoped>
.skill-node {
  position: relative; /* 徽章/锁标记绝对定位锚点 */
  width: 210px;
  border-radius: 14px;
  background: var(--cp-card);
  box-shadow: var(--cp-shadow);
  transition:
    transform 0.18s ease,
    box-shadow 0.18s ease;
}

.node-inner {
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 5px;
  align-items: flex-start;
}

.chapter-tag {
  font-size: 11px;
  font-weight: 600;
  color: var(--cp-primary);
  background: var(--cp-primary-soft);
  border-radius: 999px;
  padding: 1px 9px;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.node-title {
  font-size: 13.5px;
  font-weight: 700;
  color: var(--cp-ink);
  margin: 0;
  line-height: 1.35;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* ---- 未解锁: 灰化 + 锁标记, 不可点击 ---- */
.is-locked {
  opacity: 0.62;
  background: #f4f4f7;
  box-shadow: 0 6px 18px rgba(74, 74, 85, 0.08);
}

.is-locked .node-title,
.is-locked .chapter-tag {
  color: var(--cp-ink-soft);
}

.lock-mark {
  position: absolute;
  top: 10px;
  right: 10px;
  font-size: 13px;
}

/* ---- 可解锁: 主色淡描边 + 悬停抬升, 可点击 ---- */
.is-unlocked,
.is-in_progress {
  border: 1.5px solid rgba(251, 114, 153, 0.35);
  cursor: pointer;
}

.is-unlocked:hover,
.is-in_progress:hover {
  transform: translateY(-3px);
  box-shadow: var(--cp-shadow-hover);
}

.is-in_progress {
  border-color: var(--cp-primary);
  animation: in-progress-pulse 2s ease-in-out infinite;
}

/* ---- 已通关点亮: 渐变底 + 光晕 + 一次性点亮动画 ---- */
.is-cleared {
  background: linear-gradient(135deg, var(--cp-primary), var(--cp-accent-lilac));
  border: none;
  cursor: pointer;
  animation:
    node-light-up 0.7s cubic-bezier(0.22, 1, 0.36, 1),
    cleared-glow 2.6s ease-in-out infinite;
}

.is-cleared .node-title,
.is-cleared .chapter-tag {
  color: #fff;
}

.is-cleared .chapter-tag {
  background: rgba(255, 255, 255, 0.22);
}

.is-cleared:hover {
  transform: translateY(-3px) scale(1.02);
  box-shadow: 0 16px 44px rgba(251, 114, 153, 0.45);
}

.progress-badge {
  position: absolute;
  top: -9px;
  right: -9px;
  font-size: 11px;
  font-weight: 800;
  color: #fff;
  background: var(--cp-primary-deep);
  border-radius: 999px;
  padding: 2px 8px;
  box-shadow: 0 4px 10px rgba(251, 114, 153, 0.4);
}

/* 点亮动画: 弹起 + 光晕显现 */
@keyframes node-light-up {
  0% {
    transform: scale(0.88);
    box-shadow: 0 0 0 0 rgba(251, 114, 153, 0);
  }
  55% {
    transform: scale(1.04);
  }
  100% {
    transform: scale(1);
    box-shadow: 0 0 0 14px rgba(251, 114, 153, 0);
  }
}

@keyframes cleared-glow {
  0%,
  100% {
    box-shadow: 0 10px 28px rgba(251, 114, 153, 0.38);
  }
  50% {
    box-shadow: 0 10px 34px rgba(251, 114, 153, 0.55);
  }
}

/* 进行中呼吸提示 (Issue 05 数据流入后可见) */
@keyframes in-progress-pulse {
  0%,
  100% {
    box-shadow: var(--cp-shadow);
  }
  50% {
    box-shadow: 0 0 0 8px rgba(251, 114, 153, 0.16);
  }
}
</style>
