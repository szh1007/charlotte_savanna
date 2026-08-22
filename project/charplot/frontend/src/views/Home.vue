<script setup lang="ts">
// 首页 (Issue 03): Hero 输入区 (一句话/链接/文件) + 我的旅程 (进行中/已通关).
// 创建旅程 → 启动 FastAPI 管道 → 跳转详情页 (带 task_id, 详情页接管 SSE).
// 视觉遵循 /frontend-design: brief 已 pin down B 站粉动漫风 (theme.css 令牌),
// 签名元素 = Hero 输入区 (唯一 bold 点), 其余克制.
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, type UploadInstance } from 'element-plus'
import { ApiError, createJourney, startPipeline } from '@/api/client'
import { useAuth } from '@/stores/auth'
import { useJourneys } from '@/stores/journeys'

const router = useRouter()
const { state: auth } = useAuth()
const { state, refreshList } = useJourneys()

type InputTab = 'text' | 'link' | 'file'
const activeTab = ref<InputTab>('text')
const textContent = ref('')
const linkContent = ref('')
const file = ref<File | null>(null)
const uploading = ref(false)
const uploadRef = ref<UploadInstance>()

const submitText = computed(() => {
  if (uploading.value) return '正在生成…'
  if (activeTab.value === 'file' && file.value) return '生成闯关地图'
  if (activeTab.value === 'text' && textContent.value.trim()) return '生成闯关地图'
  if (activeTab.value === 'link' && linkContent.value.trim()) return '生成闯关地图'
  return '生成闯关地图'
})

const canSubmit = computed(() => {
  if (!auth.user) return false
  if (activeTab.value === 'file') return !!file.value
  if (activeTab.value === 'link') return !!linkContent.value.trim()
  return !!textContent.value.trim()
})

/** 进行中 = 未通关 (含生成中/可继续/失败), 已通关 = cleared (Issue 05 后非空). */
const inProgress = computed(() => (state.list ?? []).filter((j) => !j.cleared))
const cleared = computed(() => (state.list ?? []).filter((j) => j.cleared))

function statusLabel(j: { status: string }) {
  return { generating: '生成中', ready: '可继续', failed: '生成失败' }[j.status] ?? j.status
}

function statusBadgeClass(j: { status: string }) {
  return j.status
}

async function submit() {
  if (!auth.user) {
    router.push({ path: '/login', query: { redirect: '/' } })
    return
  }
  uploading.value = true
  try {
    const created = await createJourney({
      input_type: activeTab.value,
      content: activeTab.value === 'text' ? textContent.value : linkContent.value,
      file: activeTab.value === 'file' && file.value ? file.value : undefined,
    })
    // 启动 AI 管道 (file 输入无 content, FastAPI 侧契约允许)
    const { task_id } = await startPipeline(
      created.journey_id,
      activeTab.value,
      activeTab.value === 'file' ? undefined : (activeTab.value === 'text' ? textContent.value : linkContent.value),
    )
    // 详情页接管 SSE 进度 (query 带 task_id, 刷新后详情页可恢复)
    router.push({ path: `/journeys/${created.journey_id}`, query: { task_id } })
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '创建失败, 请稍后重试')
  } finally {
    uploading.value = false
  }
}

function onFileChange(uploadFile: { raw?: File }) {
  file.value = uploadFile.raw ?? null
}

function clearFile() {
  file.value = null
  uploadRef.value?.clearFiles()
}

onMounted(async () => {
  if (auth.user) {
    refreshList().catch(() => {
      /* 列表加载失败由空态提示兜底 */
    })
  }
})
</script>

<template>
  <div class="home">
    <!-- Hero: 签名元素 = 输入区 -->
    <section class="hero">
      <p class="eyebrow">CHARPLOT · 闯关学知识</p>
      <h1 class="hero-title">想学什么，一句话开始</h1>
      <p class="hero-sub">输入主题 / 链接 / 文档，AI 帮你解构成闯关地图。</p>

      <div class="composer">
        <el-tabs v-model="activeTab" class="composer-tabs" stretch>
          <el-tab-pane label="一句话" name="text" />
          <el-tab-pane label="粘贴链接" name="link" />
          <el-tab-pane label="上传文件" name="file" />
        </el-tabs>

        <div class="composer-body">
          <el-input
            v-if="activeTab === 'text'"
            v-model="textContent"
            type="textarea"
            :rows="3"
            maxlength="2000"
            show-word-limit
            placeholder="比如：我想学 Python 装饰器"
            class="composer-input"
            @keydown.enter.exact.prevent="canSubmit && submit()"
          />
          <el-input
            v-else-if="activeTab === 'link'"
            v-model="linkContent"
            placeholder="粘贴网页链接，如 https://docs.python.org/3/"
            class="composer-input"
            @keydown.enter.prevent="canSubmit && submit()"
          />
          <el-upload
            v-else
            ref="uploadRef"
            :auto-upload="false"
            :limit="1"
            accept=".txt,.md,.html,.pdf,.docx,.pptx"
            class="composer-upload"
            drag
            :on-change="onFileChange"
            :on-remove="clearFile"
          >
            <div class="upload-hint">
              <span class="upload-emoji" aria-hidden="true">📄</span>
              <p>拖拽文件到此处，或点击选择</p>
              <p class="upload-sub">支持 txt / md / html / pdf / docx / pptx</p>
            </div>
          </el-upload>
        </div>

        <el-button
          type="primary"
          size="large"
          round
          class="composer-submit"
          :loading="uploading"
          :disabled="!canSubmit"
          @click="submit"
        >
          {{ submitText }}
        </el-button>
        <p v-if="!auth.user" class="composer-tip">登录后即可创建旅程</p>
      </div>
    </section>

    <!-- 我的旅程 -->
    <section v-if="auth.user" class="journeys" aria-label="我的旅程">
      <h2 class="section-title">我的旅程</h2>

      <div v-if="!state.list" class="journey-empty">正在加载旅程…</div>

      <template v-else>
        <div v-if="inProgress.length" class="journey-group">
          <h3 class="group-title">
            进行中
            <span class="group-count">{{ inProgress.length }}</span>
          </h3>
          <ul class="journey-list">
            <li v-for="j in inProgress" :key="j.id">
              <router-link :to="`/journeys/${j.id}`" class="journey-card">
                <div class="card-main">
                  <span class="badge" :class="`badge-${statusBadgeClass(j)}`">
                    {{ statusLabel(j) }}
                  </span>
                  <h4 class="card-title">{{ j.title }}</h4>
                  <p class="card-meta">
                    {{ j.chapter_count }} 章节 · {{ j.kp_count }} 知识点 ·
                    {{ new Date(j.created_at).toLocaleDateString() }}
                  </p>
                </div>
                <span class="card-arrow" aria-hidden="true">→</span>
              </router-link>
            </li>
          </ul>
        </div>

        <div v-if="cleared.length" class="journey-group">
          <h3 class="group-title">
            已通关
            <span class="group-count">{{ cleared.length }}</span>
          </h3>
          <ul class="journey-list">
            <li v-for="j in cleared" :key="j.id">
              <router-link :to="`/journeys/${j.id}`" class="journey-card">
                <div class="card-main">
                  <span class="badge badge-cleared">已通关</span>
                  <h4 class="card-title">{{ j.title }}</h4>
                  <p class="card-meta">
                    {{ j.chapter_count }} 章节 · {{ j.kp_count }} 知识点 ·
                    {{ new Date(j.created_at).toLocaleDateString() }}
                  </p>
                </div>
                <span class="card-arrow" aria-hidden="true">→</span>
              </router-link>
            </li>
          </ul>
        </div>

        <div v-if="!inProgress.length && !cleared.length" class="journey-empty">
          <p>还没有旅程，在上方输入想学的知识开始闯关吧。</p>
        </div>
      </template>
    </section>

    <section v-else class="journeys" aria-label="我的旅程">
      <div class="journey-empty">
        <p>登录后查看你的旅程</p>
        <el-button round type="primary" @click="router.push('/login')">去登录</el-button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.home {
  max-width: 860px;
  margin: 0 auto;
}

/* ---- Hero ---- */
.hero {
  text-align: center;
  margin-bottom: 48px;
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
  margin: 0 0 28px;
}

/* ---- 输入区 (签名元素) ---- */
.composer {
  max-width: 560px;
  margin: 0 auto;
  background: var(--cp-card);
  border-radius: 24px;
  box-shadow: var(--cp-shadow);
  padding: 24px 24px 20px;
  text-align: left;
}

.composer-tabs :deep(.el-tabs__item) {
  font-weight: 600;
}

.composer-body {
  margin: 8px 0 18px;
}

.composer-input :deep(.el-textarea__inner),
.composer-input :deep(.el-input__wrapper) {
  border-radius: var(--cp-radius-sm);
  font-size: 15px;
}

.composer-upload :deep(.el-upload-dragger) {
  border-radius: var(--cp-radius-sm);
  border-style: dashed;
  padding: 22px 0;
}

.upload-hint {
  text-align: center;
}

.upload-emoji {
  font-size: 28px;
}

.upload-hint p {
  margin: 6px 0 2px;
  font-size: 14px;
  color: var(--cp-ink);
}

.upload-sub {
  font-size: 12px;
  color: var(--cp-ink-soft);
}

.composer-submit {
  width: 100%;
  font-size: 15px;
  font-weight: 700;
  height: 44px;
  box-shadow: 0 8px 20px rgba(251, 114, 153, 0.3);
}

.composer-tip {
  text-align: center;
  font-size: 12px;
  color: var(--cp-ink-soft);
  margin: 10px 0 0;
}

/* ---- 我的旅程 ---- */
.journeys {
  margin-top: 8px;
}

.section-title {
  font-size: 20px;
  font-weight: 800;
  margin: 0 0 18px;
}

.journey-group {
  margin-bottom: 26px;
}

.group-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 700;
  color: var(--cp-ink-soft);
  margin: 0 0 12px;
}

.group-count {
  background: var(--cp-primary-soft);
  color: var(--cp-primary);
  font-size: 12px;
  padding: 1px 8px;
  border-radius: 999px;
}

.journey-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.journey-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--cp-card);
  border-radius: var(--cp-radius);
  box-shadow: var(--cp-shadow);
  padding: 16px 20px;
  text-decoration: none;
  transition:
    transform 0.25s ease,
    box-shadow 0.25s ease;
}

.journey-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--cp-shadow-hover);
}

.card-main {
  min-width: 0;
}

.badge {
  display: inline-block;
  font-size: 12px;
  font-weight: 700;
  padding: 2px 10px;
  border-radius: 999px;
  margin-bottom: 6px;
}

.badge-generating {
  background: rgba(165, 207, 227, 0.25);
  color: var(--cp-accent-sky);
  animation: breathe 1.6s ease-in-out infinite;
}

.badge-ready {
  background: var(--cp-primary-soft);
  color: var(--cp-primary);
}

.badge-failed {
  background: rgba(245, 166, 35, 0.16);
  color: var(--cp-warn);
}

.badge-cleared {
  background: rgba(52, 201, 142, 0.14);
  color: var(--cp-ok);
}

.card-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--cp-ink);
  margin: 0 0 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.card-meta {
  font-size: 12px;
  color: var(--cp-ink-soft);
  margin: 0;
}

.card-arrow {
  color: var(--cp-primary);
  font-size: 18px;
  flex-shrink: 0;
  margin-left: 12px;
}

.journey-empty {
  background: var(--cp-card);
  border-radius: var(--cp-radius);
  box-shadow: var(--cp-shadow);
  padding: 32px;
  text-align: center;
  color: var(--cp-ink-soft);
  font-size: 14px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
}

.journey-empty p {
  margin: 0;
}

@keyframes breathe {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.55;
  }
}
</style>
