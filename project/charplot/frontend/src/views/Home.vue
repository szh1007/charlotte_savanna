<script setup lang="ts">
// 骨架首页: 闯关式三端状态卡 (Issue 01).
// 三张「关卡卡」依次代表 Django 业务侧 / FastAPI AI 侧 / 前端自身,
// 全部点亮即最小请求链路打通 (DESIGN.md §7 步骤 01).
import { onMounted, reactive, ref } from 'vue'
import { checkAiHealth, checkDjangoHealth, type HealthStatus } from '@/api/client'

type CheckStatus = 'checking' | 'ok' | 'degraded' | 'error'

interface BackendCard {
  no: string
  name: string
  role: string
  endpoint: string
  status: CheckStatus
  detail: { label: string; value: 'ok' | 'error' | '--' }[]
  latency: number | null
  checkedAt: string
}

const cards = reactive<BackendCard[]>([
  {
    no: '01',
    name: 'Django 业务侧',
    role: '账号 · 学习数据 · 闯关交互',
    endpoint: '/api/charplot/health',
    status: 'checking',
    detail: [
      { label: 'MySQL', value: '--' },
      { label: 'Redis', value: '--' },
    ],
    latency: null,
    checkedAt: '',
  },
  {
    no: '02',
    name: 'FastAPI AI 侧',
    role: '知识管道 · RAG · 任务系统',
    endpoint: '/ai/health',
    status: 'checking',
    detail: [{ label: 'Redis', value: '--' }],
    latency: null,
    checkedAt: '',
  },
  {
    no: '03',
    name: '前端',
    role: 'Vue 3 · Element Plus · 主题基座',
    endpoint: 'localhost:9004',
    status: 'ok',
    detail: [{ label: '主题令牌', value: 'ok' }],
    latency: null,
    checkedAt: new Date().toLocaleTimeString(),
  },
])

const allOk = ref(false)
const checking = ref(false)

function applyHealth(card: BackendCard, h: HealthStatus, startedAt: number) {
  card.latency = Math.round(performance.now() - startedAt)
  card.checkedAt = new Date().toLocaleTimeString()
  card.status = h.status === 'ok' ? 'ok' : 'degraded'
  for (const item of card.detail) {
    if (item.label === 'MySQL') item.value = h.db ?? '--'
    if (item.label === 'Redis') item.value = h.redis ?? '--'
  }
}

async function checkBackend(card: BackendCard) {
  const startedAt = performance.now()
  card.status = 'checking'
  try {
    if (card.endpoint === '/api/charplot/health') {
      applyHealth(card, await checkDjangoHealth(), startedAt)
    } else {
      applyHealth(card, await checkAiHealth(), startedAt)
    }
  } catch {
    card.status = 'error'
    card.latency = Math.round(performance.now() - startedAt)
    card.checkedAt = new Date().toLocaleTimeString()
    for (const item of card.detail) item.value = 'error'
  }
}

async function runChecks() {
  checking.value = true
  await Promise.all(cards.slice(0, 2).map((c) => checkBackend(c)))
  checking.value = false
  allOk.value = cards.every((c) => c.status === 'ok')
}

onMounted(runChecks)

function statusText(s: CheckStatus): string {
  return { checking: '检查中', ok: '联通', degraded: '降级', error: '不可达' }[s]
}
</script>

<template>
  <div class="home">
    <section class="hero">
      <p class="eyebrow">CHARPLOT · 骨架就绪检查</p>
      <h1 class="hero-title">三端已就位，开始闯关</h1>
      <p class="hero-sub">
        Django 管状态与数据，FastAPI 管 AI 能力，前端负责把两者连成一条路。
        <br />
        三张关卡卡全部点亮，即最小请求链路打通。
      </p>

      <Transition name="pop">
        <div v-if="allOk" class="banner banner-ok" role="status">
          <span class="banner-dot" aria-hidden="true"></span>
          三端联通 · 骨架可启动
        </div>
        <div v-else-if="!checking" class="banner banner-warn" role="status">
          <span class="banner-dot" aria-hidden="true"></span>
          存在降级或不可达的服务，请确认后端已启动
        </div>
      </Transition>
    </section>

    <section class="stage" aria-label="服务状态">
      <article
        v-for="card in cards"
        :key="card.no"
        class="card"
        :class="`card-${card.status}`"
      >
        <header class="card-head">
          <span class="no-badge" aria-hidden="true">{{ card.no }}</span>
          <div class="card-title">
            <h2>{{ card.name }}</h2>
            <p>{{ card.role }}</p>
          </div>
          <span class="status-light" :class="`light-${card.status}`" aria-hidden="true"></span>
        </header>

        <div class="card-body">
          <p class="endpoint">{{ card.endpoint }}</p>
          <ul class="detail-list">
            <li v-for="item in card.detail" :key="item.label">
              <span class="detail-label">{{ item.label }}</span>
              <span class="pill" :class="`pill-${item.value}`">{{ item.value }}</span>
            </li>
          </ul>
        </div>

        <footer class="card-foot">
          <span class="meta">
            {{ statusText(card.status) }}
            <template v-if="card.latency !== null"> · {{ card.latency }} ms</template>
            <template v-if="card.checkedAt"> · {{ card.checkedAt }}</template>
          </span>
          <el-button
            v-if="card.endpoint.startsWith('/')"
            size="small"
            round
            :loading="checking && card.status === 'checking'"
            @click="checkBackend(card)"
          >
            重新检查
          </el-button>
        </footer>
      </article>
    </section>

    <section class="hero" style="margin-top: 40px">
      <p class="eyebrow">NEXT · 下一关</p>
      <h2 class="hero-title" style="font-size: 24px">账号体系 · 个人主页</h2>
      <p class="hero-sub">
        注册 / 登录 + charplot_profile 自动创建，游戏化状态首次登场（Issue 02）。
      </p>
    </section>
  </div>
</template>

<style scoped>
.home {
  max-width: 960px;
  margin: 0 auto;
}

/* ---- Hero ---- */
.hero {
  text-align: center;
  margin-bottom: 36px;
}

.eyebrow {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 2px;
  color: var(--cp-primary);
  margin: 0 0 10px;
}

.hero-title {
  font-size: 40px;
  font-weight: 800;
  letter-spacing: -1px;
  margin: 0 0 14px;
  color: var(--cp-ink);
  background: linear-gradient(120deg, var(--cp-primary), var(--cp-accent-lilac));
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}

.hero-sub {
  font-size: 15px;
  line-height: 1.7;
  color: var(--cp-ink-soft);
  margin: 0;
}

/* ---- 状态横幅 ---- */
.banner {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin-top: 18px;
  padding: 8px 18px;
  border-radius: 999px;
  font-size: 14px;
  font-weight: 600;
}

.banner-ok {
  background: rgba(52, 201, 142, 0.12);
  color: #1f9d6b;
}

.banner-warn {
  background: rgba(245, 166, 35, 0.14);
  color: #c07e0c;
}

.banner-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: currentColor;
  animation: breathe 2.4s ease-in-out infinite;
}

/* ---- 关卡卡 ---- */
.stage {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 20px;
}

@media (max-width: 860px) {
  .stage {
    grid-template-columns: 1fr;
  }
}

.card {
  background: var(--cp-card);
  border-radius: var(--cp-radius);
  box-shadow: var(--cp-shadow);
  padding: 22px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  transition:
    transform 0.25s ease,
    box-shadow 0.25s ease;
}

.card:hover {
  transform: translateY(-4px);
  box-shadow: var(--cp-shadow-hover);
}

.card-head {
  display: flex;
  align-items: center;
  gap: 12px;
}

.no-badge {
  flex-shrink: 0;
  width: 40px;
  height: 40px;
  display: grid;
  place-items: center;
  border-radius: 12px;
  font-size: 14px;
  font-weight: 800;
  color: #fff;
  background: linear-gradient(135deg, var(--cp-primary), var(--cp-accent-lilac));
  box-shadow: 0 6px 14px rgba(251, 114, 153, 0.35);
}

.card-title h2 {
  margin: 0;
  font-size: 17px;
  font-weight: 700;
}

.card-title p {
  margin: 3px 0 0;
  font-size: 12px;
  color: var(--cp-ink-soft);
}

/* 状态灯: 呼吸动画 */
.status-light {
  margin-left: auto;
  width: 12px;
  height: 12px;
  border-radius: 50%;
}

.light-checking {
  background: var(--cp-accent-sky);
  animation: breathe 1.4s ease-in-out infinite;
}

.light-ok {
  background: var(--cp-ok);
  box-shadow: 0 0 0 4px rgba(52, 201, 142, 0.18);
  animation: breathe 2.4s ease-in-out infinite;
}

.light-degraded {
  background: var(--cp-warn);
  box-shadow: 0 0 0 4px rgba(245, 166, 35, 0.18);
}

.light-error {
  background: var(--cp-error);
  box-shadow: 0 0 0 4px rgba(245, 108, 108, 0.18);
}

@keyframes breathe {
  0%,
  100% {
    opacity: 1;
    transform: scale(1);
  }
  50% {
    opacity: 0.55;
    transform: scale(0.86);
  }
}

.card-body {
  flex: 1;
}

.endpoint {
  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
  font-size: 12px;
  color: var(--cp-ink-soft);
  background: var(--cp-primary-soft);
  border-radius: var(--cp-radius-sm);
  padding: 8px 10px;
  margin: 0 0 14px;
  word-break: break-all;
}

.detail-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.detail-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.detail-label {
  font-size: 13px;
  color: var(--cp-ink-soft);
}

.pill {
  font-size: 12px;
  font-weight: 700;
  padding: 2px 12px;
  border-radius: 999px;
}

.pill-ok {
  background: rgba(52, 201, 142, 0.14);
  color: #1f9d6b;
}

.pill-error {
  background: rgba(245, 108, 108, 0.14);
  color: #d44949;
}

.pill--- {
  background: rgba(138, 138, 153, 0.12);
  color: var(--cp-ink-soft);
}

.card-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-top: 1px dashed rgba(251, 114, 153, 0.2);
  padding-top: 12px;
}

.meta {
  font-size: 12px;
  color: var(--cp-ink-soft);
}

/* ---- 过渡 ---- */
.pop-enter-active {
  transition: opacity 0.3s ease, transform 0.3s ease;
}

.pop-enter-from {
  opacity: 0;
  transform: translateY(6px);
}
</style>
