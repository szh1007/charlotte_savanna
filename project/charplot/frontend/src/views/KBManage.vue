<script setup lang="ts">
// 知识库管理页: 管理员预建主题知识库.
// 创建(主题名/描述/封面) → 上传文档 → 触发索引(真实解析/切分/向量化/入库,
// SSE 阶段进度) → 状态机流转就绪; 软删可恢复; 失败可重试; 下线/上线.
// 视觉遵循 /frontend-design: 状态徽章 + 索引进度 stepper 为签名元素.
// 韧性: 索引中先探任务存活 (服务重启后任务残留但已死) → 任务丢失时给
// 「重新索引」入口 (后端 claim 对超 10 分钟陈旧任务放行重抢).
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { UploadFile } from 'element-plus'
import {
  ApiError,
  createKnowledgeBase,
  deleteKbDocument,
  getKnowledgeBase,
  getKnowledgeBases,
  probeTaskAlive,
  restoreKbDocument,
  setKbOffline,
  setKbOnline,
  startKbIndex,
  subscribeTaskEvents,
  uploadKbDocuments,
  type KbDocument,
  type KnowledgeBaseDetail,
  type KnowledgeBaseSummary,
  type PipelineEvent,
} from '@/api/client'

const kbs = ref<KnowledgeBaseSummary[]>([])
const loading = ref(false)

// ---- 创建表单 ----
const form = ref({ name: '', description: '', cover: '' })
const creating = ref(false)

// ---- 详情抽屉 ----
const drawerOpen = ref(false)
const current = ref<KnowledgeBaseDetail | null>(null)
const detailLoading = ref(false)

// ---- 索引进度 (SSE) ----
const INDEX_STAGES = [
  { key: 'parsing', label: '解析' },
  { key: 'chunking', label: '切分' },
  { key: 'embedding', label: '向量化' },
  { key: 'indexing', label: '入库' },
  { key: 'done', label: '完成' },
]
const currentStage = ref('')
const progress = ref(0)
const stageMessage = ref('')
const indexing = ref(false)
// 索引任务已丢失 (探测确认): 提示原因并放开「重新索引」入口
const taskLost = ref(false)
const closeSse = ref<(() => void) | null>(null)

// ---- 文档上传 ----
const pendingFiles = ref<UploadFile[]>([])
const uploading = ref(false)
const uploadRef = ref<{ clearFiles: () => void } | null>(null)

function onFileChange(file: UploadFile) {
  pendingFiles.value.push(file)
}

function onFileRemove(file: UploadFile) {
  pendingFiles.value = pendingFiles.value.filter((f) => f.uid !== file.uid)
}

function onExceed(files: File[]) {
  ElMessage.warning(`最多一次上传 10 个, 本次 ${files.length} 个已忽略`)
}

const stageIndex = computed(() => {
  const idx = INDEX_STAGES.findIndex((s) => s.key === currentStage.value)
  return idx === -1 ? 0 : idx
})

const STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  indexing: '索引中',
  ready: '已就绪',
  failed: '索引失败',
  offline: '已下线',
}

// indexing 也在内: 任务丢失 (服务重启) 时给人工重抢入口, 后端 claim 对
// 非陈旧任务直接跳过 (幂等), 不会造成双重索引
const canIndex = computed(() => {
  const s = current.value?.status
  return s === 'draft' || s === 'indexing' || s === 'ready' || s === 'failed'
})

const canOffline = computed(() => current.value?.status === 'ready')
const canOnline = computed(() => current.value?.status === 'offline')

async function refreshList() {
  loading.value = true
  try {
    const data = await getKnowledgeBases()
    kbs.value = data.kbs
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '加载知识库失败')
  } finally {
    loading.value = false
  }
}

async function onCreate() {
  const name = form.value.name.trim()
  if (!name) {
    ElMessage.warning('请填写主题名')
    return
  }
  creating.value = true
  try {
    const kb = await createKnowledgeBase({
      name,
      description: form.value.description.trim(),
      cover: form.value.cover.trim(),
    })
    ElMessage.success(`已创建「${kb.name}」`)
    form.value = { name: '', description: '', cover: '' }
    await refreshList()
    await openDetail(kb.id) // 创建后直接进入详情, 引导上传文档
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '创建失败, 请稍后重试')
  } finally {
    creating.value = false
  }
}

async function openDetail(id: number) {
  drawerOpen.value = true
  detailLoading.value = true
  taskLost.value = false // 每次开抽屉重置: 丢失态只属于当前库
  try {
    const detail = await getKnowledgeBase(id)
    current.value = detail
    if (detail.status === 'indexing') {
      // 任务中断后回到页面: 先探任务存活 —— 活着续推进度 (SSE),
      // 已死 (FastAPI 重启/过期) 则给「重新索引」入口, 不空转 stepper
      if ((await probeTaskAlive(detail.latest_task_id)) === false) {
        taskLost.value = true
      } else if (detail.latest_task_id) {
        beginSse(detail.latest_task_id)
      }
    }
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '加载详情失败')
  } finally {
    detailLoading.value = false
  }
}

async function reloadDetail() {
  if (!current.value) return
  const detail = await getKnowledgeBase(current.value.id).catch(() => null)
  if (detail) current.value = detail
  await refreshList()
}

// ---- 索引任务 ----

function beginSse(taskId: string) {
  closeSse.value?.()
  currentStage.value = 'parsing'
  progress.value = 0
  taskLost.value = false
  indexing.value = true
  closeSse.value = subscribeTaskEvents(taskId, {
    onEvent: (ev: PipelineEvent) => {
      currentStage.value = ev.stage
      progress.value = ev.progress
      stageMessage.value = ev.message
      if (ev.stage === 'done' || ev.stage === 'error') {
        // 任务结束 → 重载详情 (ready / failed + 错误信息)
        indexing.value = false
        reloadDetail().catch(() => {})
      }
    },
    onLost: () => {
      indexing.value = false
      taskLost.value = true
    },
  })
}

/** 触发/重试索引 (幂等由后端 claim 保证: 索引中/下线/无文档 → done 跳过). */
async function onIndex() {
  const kb = current.value
  if (!kb || !canIndex.value) return
  try {
    const { task_id } = await startKbIndex(kb.id)
    ElMessage.info('索引任务已启动')
    beginSse(task_id)
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '启动索引失败')
  }
}

// ---- 文档管理 ----

async function onUpload() {
  const kb = current.value
  if (!kb) return
  // UploadRawFile = File & {uid}, flatMap 过滤未带原始文件的项
  const files = pendingFiles.value.flatMap((f) => (f.raw ? [f.raw] : []))
  if (!files.length) {
    ElMessage.warning('请先选择文档 (pdf/docx/pptx/md/txt/html)')
    return
  }
  uploading.value = true
  try {
    const { documents } = await uploadKbDocuments(kb.id, files)
    ElMessage.success(`已上传 ${documents.length} 个文档`)
    uploadRef.value?.clearFiles()
    await reloadDetail()
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '上传失败, 请稍后重试')
  } finally {
    uploading.value = false
  }
}

async function onSoftDelete(doc: KbDocument) {
  try {
    await deleteKbDocument(doc.id)
    ElMessage.success('已移除, 可在下方恢复')
    await reloadDetail()
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '移除失败')
  }
}

async function onRestore(doc: KbDocument) {
  try {
    await restoreKbDocument(doc.id)
    ElMessage.success(`已恢复「${doc.title}」`)
    await reloadDetail()
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '恢复失败')
  }
}

async function onToggleOffline() {
  const kb = current.value
  if (!kb) return
  try {
    await (canOnline.value ? setKbOnline(kb.id) : setKbOffline(kb.id))
    ElMessage.success(canOnline.value ? '已恢复上线' : '已下线, 用户端不可见')
    await reloadDetail()
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '操作失败')
  }
}

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${Math.max(1, Math.round(bytes / 1024))} KB`
}

onMounted(() => {
  refreshList()
})

onUnmounted(() => {
  closeSse.value?.() // 必须断开 EventSource, 页面离开不泄漏
})
</script>

<template>
  <div class="kb-manage">
    <header class="head">
      <h1 class="head-title">知识库管理</h1>
      <p class="head-sub">预建主题知识库, 就绪后以主题卡片展示在首页</p>
    </header>

    <!-- 创建表单 -->
    <section class="panel create-panel">
      <h2 class="panel-title">创建知识库</h2>
      <el-form :inline="true" class="create-form" @submit.prevent>
        <el-form-item label="主题名">
          <el-input
            v-model="form.name"
            placeholder="如: RAG 实战"
            maxlength="200"
            class="field-name"
            @keyup.enter="onCreate"
          />
        </el-form-item>
        <el-form-item label="描述">
          <el-input
            v-model="form.description"
            placeholder="一句话介绍主题 (可选)"
            class="field-desc"
            @keyup.enter="onCreate"
          />
        </el-form-item>
        <el-form-item label="封面图 URL">
          <el-input
            v-model="form.cover"
            placeholder="图片链接 (可选)"
            class="field-cover"
            @keyup.enter="onCreate"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" round :loading="creating" @click="onCreate">
            创建
          </el-button>
        </el-form-item>
      </el-form>
    </section>

    <!-- 知识库列表 -->
    <section class="panel list-panel">
      <h2 class="panel-title">全部知识库</h2>
      <el-table v-loading="loading" :data="kbs" class="kb-table">
        <el-table-column prop="name" label="主题名" min-width="160">
          <template #default="{ row }">
            <span class="kb-name">{{ row.name }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <span class="status-badge" :class="`status-${row.status}`">
              {{ STATUS_LABELS[row.status] ?? row.status }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="document_count" label="文档数" width="80" />
        <el-table-column label="创建时间" width="130">
          <template #default="{ row }">
            {{ new Date(row.created_at).toLocaleDateString() }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120">
          <template #default="{ row }">
            <el-button size="small" round @click="openDetail(row.id)">
              管理
            </el-button>
          </template>
        </el-table-column>
        <template #empty>
          <div class="list-empty">
            还没有知识库
            <span class="empty-hint">使用上方表单创建第一个主题吧</span>
          </div>
        </template>
      </el-table>
    </section>

    <!-- 详情抽屉: 文档管理 + 索引进度 -->
    <el-drawer
      v-model="drawerOpen"
      size="520px"
      :title="current?.name ?? '知识库详情'"
    >
      <div v-loading="detailLoading" class="drawer-body">
        <template v-if="current">
          <div class="drawer-head">
            <span class="status-badge" :class="`status-${current.status}`">
              {{ STATUS_LABELS[current.status] ?? current.status }}
            </span>
            <div class="drawer-actions">
              <el-button
                v-if="canIndex"
                size="small"
                round
                type="primary"
                :disabled="indexing"
                @click="onIndex"
              >
                {{ current.status === 'draft' ? '触发索引' : '重新索引' }}
              </el-button>
              <el-button
                v-if="canOffline"
                size="small"
                round
                plain
                @click="onToggleOffline"
              >
                下线
              </el-button>
              <el-button
                v-if="canOnline"
                size="small"
                round
                plain
                @click="onToggleOffline"
              >
                恢复上线
              </el-button>
              <el-button size="small" round plain @click="drawerOpen = false">
                关闭
              </el-button>
            </div>
          </div>
          <p v-if="current.status === 'failed'" class="fail-message">
            {{ current.error_message || '索引失败, 点击「重新索引」重试' }}
          </p>
          <p v-if="taskLost" class="fail-message">
            索引任务已中断 (AI 服务可能重启过)。点「重新索引」重新排队;
            后台仍标记索引中时, 陈旧锁最长 10 分钟自动放开。
          </p>

          <!-- 索引进度 (真实索引阶段 SSE) -->
          <section v-if="indexing" class="index-progress" aria-label="索引进度">
            <ol class="stepper">
              <li
                v-for="(s, i) in INDEX_STAGES"
                :key="s.key"
                class="step"
                :class="{
                  'step-done': i < stageIndex,
                  'step-active': i === stageIndex,
                }"
              >
                {{ s.label }}
              </li>
            </ol>
            <el-progress :percentage="progress" :stroke-width="8" class="index-bar" />
            <p class="stage-message">{{ stageMessage }}</p>
          </section>

          <!-- 文档上传 -->
          <section class="doc-upload">
            <h3 class="drawer-section-title">上传文档</h3>
            <div class="upload-row">
              <el-upload
                ref="uploadRef"
                :auto-upload="false"
                multiple
                accept=".pdf,.docx,.pptx,.md,.txt,.html"
                :on-change="onFileChange"
                :on-remove="onFileRemove"
                :on-exceed="onExceed"
                :limit="10"
              >
                <el-button size="small" round>选择文件</el-button>
                <template #tip>
                  <div class="upload-tip">
                    支持 pdf / docx / pptx / md / txt / html, 单个 ≤ 20MB
                  </div>
                </template>
              </el-upload>
              <el-button
                size="small"
                round
                type="primary"
                :loading="uploading"
                :disabled="!pendingFiles.length"
                @click="onUpload"
              >
                上传 {{ pendingFiles.length ? `(${pendingFiles.length})` : '' }}
              </el-button>
            </div>
          </section>

          <!-- 有效文档 -->
          <section class="doc-section">
            <h3 class="drawer-section-title">
              文档 ({{ current.documents.length }})
            </h3>
            <div v-if="current.documents.length" class="doc-list">
              <div
                v-for="doc in current.documents"
                :key="doc.id"
                class="doc-item"
              >
                <div class="doc-main">
                  <span class="doc-name">{{ doc.title }}</span>
                  <span class="doc-size">{{ formatSize(doc.file_size) }}</span>
                </div>
                <el-button
                  size="small"
                  text
                  type="danger"
                  @click="onSoftDelete(doc)"
                >
                  移除
                </el-button>
              </div>
            </div>
            <p v-else class="doc-empty">暂无文档, 上传后即可触发索引</p>
          </section>

          <!-- 已删除 (回收区, 可恢复) -->
          <section
            v-if="current.deleted_documents.length"
            class="doc-section deleted-section"
          >
            <h3 class="drawer-section-title">
              已移除 ({{ current.deleted_documents.length }})
            </h3>
            <div class="doc-list">
              <div
                v-for="doc in current.deleted_documents"
                :key="doc.id"
                class="doc-item doc-item-deleted"
              >
                <div class="doc-main">
                  <span class="doc-name">{{ doc.title }}</span>
                  <span class="doc-size">{{ formatSize(doc.file_size) }}</span>
                </div>
                <el-button size="small" text @click="onRestore(doc)">
                  恢复
                </el-button>
              </div>
            </div>
          </section>
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.kb-manage {
  max-width: 960px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.head-title {
  margin: 0;
  font-size: 28px;
  color: var(--cp-ink);
  letter-spacing: -0.5px;
}

.head-sub {
  margin: 6px 0 0;
  color: var(--cp-ink-soft);
  font-size: 14px;
}

.panel {
  background: var(--cp-card);
  border-radius: var(--cp-radius);
  box-shadow: var(--cp-shadow);
  padding: 24px;
}

.panel-title {
  margin: 0 0 16px;
  font-size: 17px;
  color: var(--cp-ink);
}

.create-form {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 16px;
}

.create-form :deep(.el-form-item) {
  margin-bottom: 8px;
}

.field-name {
  width: 180px;
}

.field-desc {
  width: 240px;
}

.field-cover {
  width: 220px;
}

.kb-table {
  --el-table-border-color: rgba(251, 114, 153, 0.08);
  --el-table-header-bg-color: var(--cp-primary-soft);
}

.kb-name {
  font-weight: 600;
  color: var(--cp-ink);
}

/* 状态徽章: 签名元素 (索引中呼吸动画, 与首页生成进度呼应) */
.status-badge {
  display: inline-flex;
  align-items: center;
  font-size: 12px;
  font-weight: 700;
  padding: 4px 12px;
  border-radius: 999px;
  background: var(--cp-card);
  border: 1px solid rgba(74, 74, 85, 0.15);
  color: var(--cp-ink-soft);
}

.status-indexing {
  color: var(--cp-primary-deep);
  background: var(--cp-primary-soft);
  border-color: rgba(251, 114, 153, 0.35);
  animation: statusPulse 1.4s ease-in-out infinite;
}

.status-ready {
  color: var(--cp-ok);
  background: rgba(52, 201, 142, 0.1);
  border-color: rgba(52, 201, 142, 0.3);
}

.status-failed {
  color: var(--cp-warn);
  background: rgba(245, 166, 35, 0.1);
  border-color: rgba(245, 166, 35, 0.3);
}

@keyframes statusPulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.55;
  }
}

.list-empty {
  padding: 28px 0;
  color: var(--cp-ink-soft);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.empty-hint {
  font-size: 13px;
  color: var(--cp-primary-deep);
}

/* ---- 抽屉 ---- */
.drawer-body {
  min-height: 200px;
}

.drawer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}

.drawer-actions {
  display: flex;
  gap: 8px;
}

.fail-message {
  margin: 0 0 14px;
  padding: 10px 14px;
  border-radius: var(--cp-radius-sm);
  background: rgba(245, 166, 35, 0.1);
  color: var(--cp-warn);
  font-size: 13px;
}

/* 索引进度 stepper (与旅程生成页同款) */
.index-progress {
  margin-bottom: 22px;
  padding: 16px;
  border-radius: var(--cp-radius-sm);
  background: var(--cp-primary-soft);
}

.stepper {
  display: flex;
  gap: 4px;
  list-style: none;
  padding: 0;
  margin: 0 0 12px;
}

.step {
  flex: 1;
  text-align: center;
  font-size: 12px;
  font-weight: 600;
  color: var(--cp-ink-soft);
  padding: 6px 4px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.7);
  transition:
    background 0.3s ease,
    color 0.3s ease;
}

.step-done {
  color: var(--cp-card);
  background: var(--cp-primary);
}

.step-active {
  color: var(--cp-primary-deep);
  background: var(--cp-card);
  box-shadow: 0 0 0 2px rgba(251, 114, 153, 0.35);
}

.index-bar {
  margin-bottom: 8px;
}

.stage-message {
  margin: 0;
  font-size: 13px;
  color: var(--cp-ink-soft);
}

/* ---- 文档区 ---- */
.drawer-section-title {
  margin: 0 0 10px;
  font-size: 15px;
  color: var(--cp-ink);
}

.doc-upload {
  margin-bottom: 22px;
}

.upload-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.upload-tip {
  margin-top: 6px;
  font-size: 12px;
  color: var(--cp-ink-soft);
}

.doc-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.doc-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 14px;
  border-radius: var(--cp-radius-sm);
  background: var(--cp-primary-soft);
}

.doc-item-deleted {
  background: rgba(74, 74, 85, 0.06);
}

.doc-main {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}

.doc-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--cp-ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.doc-item-deleted .doc-name {
  color: var(--cp-ink-soft);
  text-decoration: line-through;
}

.doc-size {
  font-size: 12px;
  color: var(--cp-ink-soft);
  white-space: nowrap;
}

.doc-empty {
  margin: 0;
  padding: 14px;
  text-align: center;
  font-size: 13px;
  color: var(--cp-ink-soft);
  border: 1px dashed rgba(251, 114, 153, 0.3);
  border-radius: var(--cp-radius-sm);
}

.deleted-section {
  margin-top: 22px;
}
</style>
