<script setup lang="ts">
// 复盘报告页 (Issue 06, PRD E-1/E-2): 通关成果展示 (知识总结 + 答题表现)
// + 公开分享链接 (slug URL + OG 卡片说明, 未登录可访问, 只读).
// 视觉遵循 /frontend-design: 正确率环形徽章为签名元素 (成果证据即页面的
// 第一特征), 分享卡片置于浏览成果之后 (分享是看完之后的动作).
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  ApiError,
  getReviewReport,
  type ReviewReport,
} from '@/api/client'

const route = useRoute()
const router = useRouter()
const journeyId = computed(() => Number(route.params.id))

const report = ref<ReviewReport | null>(null)
const loading = ref(true)
const copied = ref(false)

onMounted(async () => {
  try {
    report.value = await getReviewReport(journeyId.value)
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '报告加载失败, 请稍后重试')
  } finally {
    loading.value = false
  }
})

/** 完整公开分享链接 (相对 share_url 拼 origin). */
const shareUrl = computed(() =>
  report.value ? `${location.origin}${report.value.share_url}` : '',
)

/** 环形徽章: conic-gradient 按正确率填充, 中心盖圆环 (签名元素). */
const ringStyle = computed(() => {
  const accuracy = report.value?.stats.accuracy ?? 0
  return {
    background: `conic-gradient(var(--cp-primary) ${accuracy}%, var(--cp-primary-soft) ${accuracy}% 100%)`,
  }
})

async function copyLink() {
  if (!shareUrl.value) return
  try {
    await navigator.clipboard.writeText(shareUrl.value)
    copied.value = true
    ElMessage.success('分享链接已复制')
    setTimeout(() => (copied.value = false), 2000)
  } catch {
    // 非安全上下文等场景 clipboard 不可用, 提示手动复制
    ElMessage.info('请手动长按选择复制链接')
  }
}

function formatDuration(seconds: number) {
  if (seconds < 60) return `${seconds}s`
  const min = Math.floor(seconds / 60)
  const sec = seconds % 60
  return sec ? `${min} 分 ${sec} 秒` : `${min} 分钟`
}
</script>

<template>
  <div class="report">
    <router-link :to="`/journeys/${journeyId}/map`" class="back">
      ← 返回闯关地图
    </router-link>

    <!-- 加载中 / 异常 -->
    <section v-if="loading" class="panel">
      <el-skeleton :rows="5" animated />
    </section>
    <section v-else-if="!report" class="panel empty">
      <p class="empty-emoji" aria-hidden="true">📜</p>
      <h2 class="empty-title">还没有复盘报告</h2>
      <p class="empty-detail">通关旅程的全部关卡后, 这里会生成你的通关总结。</p>
      <el-button type="primary" round @click="router.push(`/journeys/${journeyId}/map`)">
        去闯关
      </el-button>
    </section>

    <template v-else>
      <!-- Hero: 正确率环形徽章 (签名元素) + 通关信息 -->
      <header class="hero panel">
        <p class="hero-eyebrow">通关复盘报告</p>
        <h1 class="hero-title">{{ report.og_title.replace(' · 通关复盘', '') }}</h1>
        <p class="hero-meta">
          {{ new Date(report.created_at).toLocaleDateString() }} 通关
        </p>
        <div class="ring" :style="ringStyle" role="img" :aria-label="`正确率 ${report.stats.accuracy}%`">
          <div class="ring-inner">
            <span class="ring-num">{{ report.stats.accuracy }}%</span>
            <span class="ring-label">正确率</span>
          </div>
        </div>
        <div class="stats-grid">
          <div class="stat">
            <span class="stat-num">{{ report.stats.answered }}</span>
            <span class="stat-label">答题</span>
          </div>
          <div class="stat">
            <span class="stat-num ok">{{ report.stats.correct }}</span>
            <span class="stat-label">答对</span>
          </div>
          <div class="stat">
            <span class="stat-num warn">{{ report.stats.wrong }}</span>
            <span class="stat-label">答错</span>
          </div>
          <div class="stat">
            <span class="stat-num">{{ formatDuration(report.stats.duration) }}</span>
            <span class="stat-label">总耗时</span>
          </div>
        </div>
      </header>

      <!-- 关卡表现: 每知识点答题对错 (重开历史并入统计) -->
      <section v-if="report.stats.levels.length" class="panel">
        <h2 class="section-title"><span class="emoji" aria-hidden="true">📊</span>关卡表现</h2>
        <ul class="level-list">
          <li
            v-for="item in report.stats.levels"
            :key="item.level_id"
            class="level-item"
          >
            <div class="level-info">
              <span class="level-chapter">{{ item.chapter_title }}</span>
              <span class="level-kp">{{ item.kp_title }}</span>
            </div>
            <span
              class="level-acc"
              :class="{ low: item.answered > 0 && item.correct / item.answered < 0.6 }"
            >
              {{ item.correct }}/{{ item.answered }}
            </span>
          </li>
        </ul>
      </section>

      <!-- 知识总结: 章节 → 知识点 -->
      <section class="panel">
        <h2 class="section-title"><span class="emoji" aria-hidden="true">📚</span>知识总结</h2>
        <div
          v-for="(chapter, ci) in report.knowledge_summary.chapters"
          :key="ci"
          class="chapter"
        >
          <h3 class="chapter-name">{{ chapter.title }}</h3>
          <p v-if="chapter.summary" class="chapter-summary">{{ chapter.summary }}</p>
          <ul class="kp-list">
            <li v-for="(kp, ki) in chapter.knowledge_points" :key="ki" class="kp-item">
              <span class="kp-name">{{ kp.title }}</span>
              <span v-if="kp.summary" class="kp-summary">{{ kp.summary }}</span>
            </li>
          </ul>
        </div>
      </section>

      <!-- 分享: 公开只读链接 + OG 卡片说明 (PRD E-2) -->
      <section class="panel share">
        <h2 class="section-title"><span class="emoji" aria-hidden="true">🔗</span>分享复盘报告</h2>
        <p class="share-desc">
          生成公开链接, 任何人无需登录即可查看这份报告; 分享到社交平台会带
          标题、摘要和缩略图卡片。
        </p>
        <div class="share-row">
          <el-input :model-value="shareUrl" readonly class="share-input" aria-label="分享链接" />
          <el-button type="primary" round @click="copyLink">
            {{ copied ? '已复制 ✓' : '复制链接' }}
          </el-button>
        </div>
        <p class="share-tip">链接内容已固定, 只读展示, 无法被修改。</p>
      </section>
    </template>
  </div>
</template>

<style scoped>
.report {
  max-width: 680px;
  margin: 0 auto;
}

.back {
  display: inline-block;
  font-size: 13px;
  color: var(--cp-ink-soft);
  text-decoration: none;
  margin-bottom: 16px;
}

.back:hover {
  color: var(--cp-primary);
}

.panel {
  background: var(--cp-card);
  border-radius: var(--cp-radius);
  box-shadow: var(--cp-shadow);
  padding: 28px 26px;
  margin-bottom: 16px;
}

/* ---- Hero: 正确率环形徽章 (签名元素) ---- */
.hero {
  text-align: center;
}

.hero-eyebrow {
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 2px;
  color: var(--cp-primary);
  margin: 0 0 6px;
}

.hero-title {
  font-size: 24px;
  font-weight: 800;
  color: var(--cp-ink);
  margin: 0 0 4px;
  word-break: break-all;
}

.hero-meta {
  font-size: 13px;
  color: var(--cp-ink-soft);
  margin: 0 0 22px;
}

.ring {
  width: 148px;
  height: 148px;
  border-radius: 50%;
  margin: 0 auto 20px;
  display: grid;
  place-items: center;
  animation: ringPop 0.7s ease-out;
}

@keyframes ringPop {
  0% {
    transform: scale(0.5);
    opacity: 0;
  }
  70% {
    transform: scale(1.06);
  }
  100% {
    transform: scale(1);
    opacity: 1;
  }
}

.ring-inner {
  width: 118px;
  height: 118px;
  border-radius: 50%;
  background: var(--cp-card);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
}

.ring-num {
  font-size: 36px;
  font-weight: 900;
  color: var(--cp-primary);
  line-height: 1.1;
}

.ring-label {
  font-size: 12px;
  color: var(--cp-ink-soft);
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
}

.stat {
  background: var(--cp-primary-soft);
  border-radius: var(--cp-radius-sm);
  padding: 12px 4px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.stat-num {
  font-size: 20px;
  font-weight: 900;
  color: var(--cp-ink);
}

.stat-num.ok {
  color: var(--cp-ok);
}

.stat-num.warn {
  color: var(--cp-warn);
}

.stat-label {
  font-size: 12px;
  color: var(--cp-ink-soft);
}

/* ---- 通用区段 ---- */
.section-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 17px;
  font-weight: 800;
  color: var(--cp-ink);
  margin: 0 0 16px;
}

.section-title .emoji {
  font-size: 20px;
}

/* ---- 关卡表现 ---- */
.level-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.level-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  background: var(--cp-primary-soft);
  border-radius: var(--cp-radius-sm);
  padding: 12px 14px;
}

.level-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.level-chapter {
  font-size: 11px;
  font-weight: 600;
  color: var(--cp-accent-sky);
}

.level-kp {
  font-size: 14px;
  font-weight: 700;
  color: var(--cp-ink);
}

.level-acc {
  flex-shrink: 0;
  font-size: 15px;
  font-weight: 800;
  color: var(--cp-ok);
}

.level-acc.low {
  color: var(--cp-warn);
}

/* ---- 知识总结 ---- */
.chapter {
  margin-bottom: 14px;
}

.chapter:last-child {
  margin-bottom: 0;
}

.chapter-name {
  font-size: 15px;
  font-weight: 800;
  color: var(--cp-primary);
  margin: 0 0 4px;
}

.chapter-summary {
  font-size: 12px;
  color: var(--cp-ink-soft);
  margin: 0 0 10px;
  line-height: 1.6;
}

.kp-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.kp-item {
  display: flex;
  flex-direction: column;
  gap: 3px;
  background: var(--cp-primary-soft);
  border-radius: var(--cp-radius-sm);
  padding: 10px 14px;
}

.kp-name {
  font-size: 13px;
  font-weight: 700;
  color: var(--cp-ink);
}

.kp-summary {
  font-size: 12px;
  color: var(--cp-ink-soft);
  line-height: 1.6;
}

/* ---- 分享 ---- */
.share-desc {
  font-size: 13px;
  color: var(--cp-ink-soft);
  margin: 0 0 14px;
  line-height: 1.7;
}

.share-row {
  display: flex;
  gap: 10px;
}

.share-input {
  flex: 1;
}

.share-tip {
  font-size: 12px;
  color: var(--cp-ink-soft);
  margin: 10px 0 0;
}

/* ---- 空态 ---- */
.empty {
  text-align: center;
}

.empty-emoji {
  font-size: 44px;
  margin: 0 0 8px;
}

.empty-title {
  font-size: 18px;
  font-weight: 800;
  color: var(--cp-ink);
  margin: 0 0 6px;
}

.empty-detail {
  font-size: 13px;
  color: var(--cp-ink-soft);
  margin: 0 0 18px;
}
</style>
