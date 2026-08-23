<script setup lang="ts">
// 关卡入口页 (Issue 04 占位 → Issue 05 完整实现).
// 本期承接闯关地图的节点点击: 展示目标知识点 (标题/摘要) 与「闯关系统开发中」提示,
// 保证地图页「点击可解锁节点进入关卡入口」验收闭环; 答题/判分等能力 Issue 05 落地.
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  ApiError,
  getJourney,
  type JourneyDetail,
  type KnowledgePoint,
} from '@/api/client'

const route = useRoute()
const journeyId = computed(() => Number(route.params.id))
const kpId = computed(() => Number(route.query.kp) || 0)

const detail = ref<JourneyDetail | null>(null)

/** 从旅程图谱中定位目标知识点 (含章节归属). */
const kp = computed<{ kp: KnowledgePoint; chapterTitle: string } | null>(() => {
  for (const chapter of detail.value?.chapters ?? []) {
    const found = chapter.knowledge_points.find((p) => p.id === kpId.value)
    if (found) return { kp: found, chapterTitle: chapter.title }
  }
  return null
})

onMounted(async () => {
  try {
    detail.value = await getJourney(journeyId.value)
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '加载失败, 请稍后重试')
  }
})
</script>

<template>
  <div class="level-page">
    <router-link :to="`/journeys/${journeyId}/map`" class="back">← 返回闯关地图</router-link>

    <section class="panel">
      <template v-if="kp">
        <span class="chapter-tag">{{ kp.chapterTitle }}</span>
        <h1 class="kp-title">{{ kp.kp.title }}</h1>
        <p v-if="kp.kp.summary" class="kp-summary">{{ kp.kp.summary }}</p>
      </template>

      <p class="build-emoji" aria-hidden="true">🎮</p>
      <h2 class="build-title">关卡系统开发中</h2>
      <p class="build-detail">
        这个知识点的闯关题目正在生成, 下一版本即可答题点亮技能树。
      </p>

      <div class="actions">
        <el-button type="primary" round disabled>开始闯关</el-button>
        <el-button round @click="$router.push(`/journeys/${journeyId}/map`)">
          返回地图
        </el-button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.level-page {
  max-width: 640px;
  margin: 0 auto;
}

.back {
  display: inline-block;
  font-size: 13px;
  color: var(--cp-ink-soft);
  text-decoration: none;
  margin-bottom: 18px;
}

.back:hover {
  color: var(--cp-primary);
}

.panel {
  background: var(--cp-card);
  border-radius: var(--cp-radius);
  box-shadow: var(--cp-shadow);
  padding: 40px 32px;
  text-align: center;
}

.chapter-tag {
  display: inline-block;
  font-size: 12px;
  font-weight: 600;
  color: var(--cp-primary);
  background: var(--cp-primary-soft);
  border-radius: 999px;
  padding: 2px 12px;
  margin-bottom: 10px;
}

.kp-title {
  font-size: 24px;
  font-weight: 800;
  margin: 0 0 6px;
  color: var(--cp-ink);
}

.kp-summary {
  font-size: 13px;
  color: var(--cp-ink-soft);
  margin: 0 0 22px;
  line-height: 1.6;
}

.build-emoji {
  font-size: 40px;
  margin: 0 0 6px;
}

.build-title {
  font-size: 17px;
  font-weight: 700;
  margin: 0 0 6px;
}

.build-detail {
  font-size: 13px;
  color: var(--cp-ink-soft);
  margin: 0 0 20px;
}

.actions {
  display: flex;
  justify-content: center;
  gap: 10px;
}
</style>
