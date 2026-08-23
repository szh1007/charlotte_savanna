<script setup lang="ts">
// 心动值条形 (Issue 05): 满心彩色, 已扣心置灰.
// 扣心瞬间有「心飞走了」动画 (缩小上浮淡出), 答错的温和反馈核心元素
// (DESIGN.md §6: 答错是鼓励而非红叉).
import { computed, ref, watch } from 'vue'

const props = defineProps<{ hearts: number; max?: number }>()

const max = computed(() => props.max ?? 5)
const lost = computed(() => max.value - props.hearts)

// 最近一次被扣的心下标 (0-based), 短暂置位触发飞走动画后清除
const flying = ref(-1)
let lastHearts = props.hearts
watch(
  () => props.hearts,
  (now) => {
    if (now < lastHearts) {
      flying.value = max.value - now - 1
      setTimeout(() => {
        flying.value = -1
      }, 700)
    }
    lastHearts = now
  },
)
</script>

<template>
  <div class="hearts" role="img" :aria-label="`心动值 ${hearts}/${max}`">
    <span
      v-for="i in max"
      :key="i"
      class="heart"
      :class="{ 'is-lost': i > hearts, 'is-flying': i - 1 === flying }"
      aria-hidden="true"
    >
      <svg viewBox="0 0 24 24" width="22" height="22">
        <path
          d="M12 21.3 4.2 14a5.4 5.4 0 0 1 0-7.7 5.6 5.6 0 0 1 7.8 0l.9.9.9-.9a5.6 5.6 0 0 1 7.8 0 5.4 5.4 0 0 1 0 7.7L12 21.3z"
          fill="currentColor"
        />
      </svg>
    </span>
  </div>
</template>

<style scoped>
.hearts {
  display: flex;
  gap: 4px;
}

.heart {
  color: var(--cp-primary);
  display: inline-flex;
  transition:
    color 0.35s ease,
    transform 0.35s ease;
}

.heart.is-lost {
  color: #dcdce4;
  transform: scale(0.86);
}

/* 扣心飞走: 缩小上浮淡出 (一次性动画) */
.heart.is-flying {
  animation: heartFly 0.7s ease-out;
}

@keyframes heartFly {
  0% {
    transform: scale(1.2);
    opacity: 1;
  }
  60% {
    transform: scale(0.9) translateY(-14px);
    opacity: 0.6;
  }
  100% {
    transform: scale(0.86) translateY(-4px);
    opacity: 1;
  }
}
</style>
