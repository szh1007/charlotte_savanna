<script setup lang="ts">
// 双后端健康指示: 挂载即探一次 + 每 60s 轮询, 异常变红可悬停看原因.
// 意义: AI 端不可达 / 降级 (rerank 未加载模型) 是**可见状态**, 而不是等
// 某个操作失败才发现; 探测失败 (网络抖动) 时保持上次结论, 不误报.
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { checkAiHealth, checkDjangoHealth, type HealthStatus } from '@/api/client'

const POLL_MS = 60000

// 两个后端各一个探针 (null = 探测失败/首次不可达)
const djangoHealth = ref<HealthStatus | null>(null)
const aiHealth = ref<HealthStatus | null>(null)
let timer: ReturnType<typeof setInterval> | null = null

/** 载荷 → 悬停文案 (含 rerank 降级原因等运行时事实). */
function describe(label: string, health: HealthStatus | null): string {
  if (!health) return `${label}: 不可达`
  const parts = [`状态 ${health.status}`]
  if (health.db) parts.push(`db ${health.db}`)
  if (health.redis) parts.push(`redis ${health.redis}`)
  if (health.rerank) {
    parts.push(
      health.rerank.degraded
        ? `rerank 降级不精排 (${health.rerank.reason ?? '未配置'})`
        : 'rerank 精排',
    )
  }
  return `${label}: ${parts.join(', ')}`
}

async function refresh() {
  const [djangoRes, aiRes] = await Promise.allSettled([
    checkDjangoHealth(),
    checkAiHealth(),
  ])
  // 网络失败保留上次结论 (已连上过就沿用, 不因一次抖动变红)
  if (djangoRes.status === 'fulfilled') djangoHealth.value = djangoRes.value
  if (aiRes.status === 'fulfilled') aiHealth.value = aiRes.value
}

const pills = computed(() =>
  [
    {
      key: 'django',
      short: '业务',
      label: '业务端',
      health: djangoHealth.value,
    },
    { key: 'ai', short: 'AI', label: 'AI 端', health: aiHealth.value },
  ].map(({ key, short, label, health }) => ({
    key,
    short,
    // 探测失败或载荷 degraded 都算异常
    down: health === null || health.status !== 'ok',
    title: describe(label, health),
  })),
)

onMounted(() => {
  refresh()
  timer = setInterval(refresh, POLL_MS)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<template>
  <div class="health" role="status" aria-label="双后端健康状态">
    <span
      v-for="pill in pills"
      :key="pill.key"
      class="pill"
      :class="{ 'is-down': pill.down }"
      :title="pill.title"
    >
      <span class="dot" aria-hidden="true"></span>{{ pill.short }}
    </span>
  </div>
</template>

<style scoped>
.health {
  display: flex;
  align-items: center;
  gap: 6px;
}

.pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  font-weight: 700;
  color: var(--cp-ink-soft);
  background: var(--cp-card);
  border: 1px solid rgba(74, 74, 85, 0.12);
  padding: 3px 8px;
  border-radius: 999px;
  cursor: default;
}

.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--cp-ok);
}

/* 异常: 红点 + 弱红底 (悬停标题给出具体原因) */
.pill.is-down {
  color: var(--cp-warn);
  border-color: rgba(245, 166, 35, 0.35);
  background: rgba(245, 166, 35, 0.1);
}

.pill.is-down .dot {
  background: var(--cp-warn);
}
</style>
