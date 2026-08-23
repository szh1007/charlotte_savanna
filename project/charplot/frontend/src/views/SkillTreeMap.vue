<script setup lang="ts">
// 闯关地图页 (Issue 04, PRD D-1): 技能树可视化 (节点 + 依赖边 + 点亮状态).
// 数据源 GET /api/charplot/journeys/{id}/skill-tree/; 点亮状态由后端计算
// (本期无通关数据, 依赖未满足锁定; Issue 05 通关后自然点亮).
// 旅程未就绪 (生成中/失败) 时引导回详情页; 点击解锁节点进入关卡入口 (Issue 05 占位页).
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  ApiError,
  getJourney,
  getSkillTree,
  type JourneyDetail,
  type SkillTreeData,
} from '@/api/client'
import SkillTree from '@/components/SkillTree.vue'

const route = useRoute()
const router = useRouter()
const journeyId = computed(() => Number(route.params.id))

const detail = ref<JourneyDetail | null>(null)
const tree = ref<SkillTreeData | null>(null)
const loading = ref(true)

/** 已点亮节点数 (完成度统计头部). */
const clearedCount = computed(
  () => tree.value?.nodes.filter((n) => n.status === 'cleared').length ?? 0,
)
const totalCount = computed(() => tree.value?.nodes.length ?? 0)

/** 点击解锁/已通关节点 → 关卡入口 (LevelList 占位页, Issue 05 完整实现). */
function openLevel(nodeId: number) {
  router.push({
    name: 'level-list',
    params: { id: journeyId.value },
    query: { kp: String(nodeId) },
  })
}

onMounted(async () => {
  try {
    const d = await getJourney(journeyId.value)
    detail.value = d
    if (d.status === 'ready') {
      tree.value = await getSkillTree(journeyId.value)
    }
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '地图加载失败, 请稍后重试')
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="map-page">
    <router-link :to="`/journeys/${journeyId}`" class="back">← 旅程详情</router-link>

    <header v-if="detail" class="map-head">
      <div class="head-text">
        <h1 class="map-title">{{ detail.title }}</h1>
        <p class="map-sub">
          闯关地图 · 已点亮 {{ clearedCount }} / {{ totalCount }} 个知识点
        </p>
      </div>
      <div class="map-legend" aria-label="图例">
        <span class="legend-item">
          <span class="legend-dot is-locked" aria-hidden="true"></span>未解锁
        </span>
        <span class="legend-item">
          <span class="legend-dot is-unlocked" aria-hidden="true"></span>可挑战
        </span>
        <span class="legend-item">
          <span class="legend-dot is-cleared" aria-hidden="true"></span>已通关
        </span>
      </div>
    </header>

    <!-- 未就绪: 引导回详情页看生成进度 / 重试 -->
    <section v-if="!loading && detail && detail.status !== 'ready'" class="empty panel">
      <p class="empty-emoji" aria-hidden="true">
        {{ detail.status === 'failed' ? '💔' : '🌱' }}
      </p>
      <h2 class="empty-title">
        {{ detail.status === 'failed' ? '图谱生成失败' : '图谱还在生成中' }}
      </h2>
      <p class="empty-detail">
        {{ detail.status === 'failed' ? detail.error_message : '去旅程详情页查看生成进度, 完成后即可闯关' }}
      </p>
      <el-button type="primary" round @click="router.push(`/journeys/${journeyId}`)">
        返回旅程详情
      </el-button>
    </section>

    <!-- 空图谱: 数据就绪但无节点 -->
    <section
      v-else-if="!loading && tree && tree.nodes.length === 0"
      class="empty panel"
    >
      <p class="empty-emoji" aria-hidden="true">🗺️</p>
      <h2 class="empty-title">地图还空着</h2>
      <p class="empty-detail">图谱没有产出知识点, 稍后重试或重新生成。</p>
      <el-button type="primary" round @click="router.push(`/journeys/${journeyId}`)">
        返回旅程详情
      </el-button>
    </section>

    <!-- 地图: 技能树画布 -->
    <section v-else-if="tree" class="map-canvas">
      <SkillTree :tree="tree" @select="openLevel" />
    </section>

    <section v-else-if="loading" class="empty panel">
      <el-skeleton :rows="3" animated />
    </section>

    <section v-else class="empty panel">
      <p class="empty-emoji" aria-hidden="true">😿</p>
      <h2 class="empty-title">旅程不存在或已删除</h2>
      <el-button round @click="router.push('/')">返回首页</el-button>
    </section>
  </div>
</template>

<style scoped>
.map-page {
  max-width: 1200px;
  margin: 0 auto;
  padding: 0 16px;
}

.back {
  display: inline-block;
  font-size: 13px;
  color: var(--cp-ink-soft);
  text-decoration: none;
  margin-bottom: 14px;
}

.back:hover {
  color: var(--cp-primary);
}

.map-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 16px;
}

.map-title {
  font-size: 26px;
  font-weight: 800;
  margin: 0 0 4px;
  color: var(--cp-ink);
}

.map-sub {
  font-size: 13px;
  color: var(--cp-ink-soft);
  margin: 0;
}

/* ---- 图例: 状态色编码 ---- */
.map-legend {
  display: flex;
  gap: 14px;
  background: var(--cp-card);
  border-radius: 999px;
  box-shadow: var(--cp-shadow);
  padding: 7px 16px;
}

.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--cp-ink-soft);
}

.legend-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  display: inline-block;
}

.legend-dot.is-locked {
  background: #d6d6de;
}

.legend-dot.is-unlocked {
  background: var(--cp-card);
  border: 1.5px solid var(--cp-primary);
}

.legend-dot.is-cleared {
  background: linear-gradient(135deg, var(--cp-primary), var(--cp-accent-lilac));
}

/* ---- 画布: 地图主体, 撑满视口剩余高度 ---- */
.map-canvas {
  height: calc(100vh - 190px);
  min-height: 460px;
  border-radius: var(--cp-radius);
  overflow: hidden;
  box-shadow: var(--cp-shadow);
  background: var(--cp-card);
}

/* ---- 空态 / 未就绪 ---- */
.empty {
  margin-top: 24px;
}

.empty-emoji {
  font-size: 40px;
  margin: 0 0 8px;
}

.empty-title {
  font-size: 18px;
  font-weight: 700;
  margin: 0 0 6px;
}

.empty-detail {
  font-size: 13px;
  color: var(--cp-ink-soft);
  margin: 0 0 18px;
}

.panel {
  background: var(--cp-card);
  border-radius: var(--cp-radius);
  box-shadow: var(--cp-shadow);
  padding: 32px;
  text-align: center;
}
</style>
