<script setup lang="ts">
// 彩花粒子动画 (通关结算 / 答对反馈, PRD §6 动效).
// 纯 CSS 实现零依赖: burst 值变化时重放一次粒子雨, 主题色系 (粉/紫/蓝/金).
import { computed } from 'vue'

const props = defineProps<{ burst: number }>()

const COLORS = ['#fb7299', '#c9b6e4', '#a5cfe3', '#ffb400', '#34c98e']

/** 确定性伪随机: 每次 burst 重放, 粒子布局固定 (避免渲染闪烁). */
function seeded(seed: number) {
  let s = seed
  return () => {
    s = (s * 9301 + 49297) % 233280
    return s / 233280
  }
}

/** burst 值作为随机种子: 不同次触发布局不同, 同一次渲染稳定. */
const particles = computed(() => {
  const rand = seeded(props.burst * 7919 + 13)
  return Array.from({ length: 42 }, (_, i) => {
    const r = rand()
    const color = COLORS[Math.floor(rand() * COLORS.length)]
    return {
      id: i,
      color,
      // 从左 2% 到 98% 飘落, 延迟错开形成雨幕
      left: 2 + r * 94,
      delay: rand() * 0.9,
      duration: 2.2 + rand() * 1.8,
      size: 6 + rand() * 7,
      // 自旋方向随机 (水平翻转区分)
      flip: rand() > 0.5 ? 1 : -1,
    }
  })
})
</script>

<template>
  <!-- :key=burst 强制重挂, 每次触发重放动画 -->
  <div v-if="burst > 0" :key="burst" class="confetti" aria-hidden="true">
    <span
      v-for="p in particles"
      :key="p.id"
      class="confetti-piece"
      :style="{
        left: `${p.left}%`,
        background: p.color,
        width: `${p.size}px`,
        height: `${p.size * 0.45}px`,
        animationDelay: `${p.delay}s`,
        animationDuration: `${p.duration}s`,
        '--flip': p.flip,
      }"
    />
  </div>
</template>

<style scoped>
.confetti {
  position: fixed;
  inset: 0;
  pointer-events: none;
  overflow: hidden;
  z-index: 40;
}

.confetti-piece {
  position: absolute;
  top: -24px;
  border-radius: 3px;
  opacity: 0;
  animation: confettiFall 2.6s ease-in forwards;
  transform: rotate(0deg) scaleX(var(--flip, 1));
}

@keyframes confettiFall {
  0% {
    opacity: 1;
    transform: translateY(0) rotate(0deg) scaleX(var(--flip, 1));
  }
  100% {
    opacity: 0;
    transform: translateY(108vh) rotate(720deg) scaleX(var(--flip, 1));
  }
}
</style>
