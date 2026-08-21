<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import ErrorAlert from './ErrorAlert.vue'

// 解析结果卡: 封面 + 标题 + 平台徽章 + 时长
// + 清晰度下拉 (锁定档带 🔒) + 「开始下载」按钮 (T08)
const props = defineProps({
  // ResolveResponse 字段:
  // task_id/title/cover/duration/site/formats/member_limited
  result: { type: Object, required: true },
  // 解析来源链接: 「开始下载」发起请求用
  url: { type: String, default: '' },
  // 下载任务创建中 (防重复点击)
  downloading: { type: Boolean, default: false },
  // 创建下载任务失败的错误信息 (父组件透传,
  // 如队列超限/档位锁定)
  downloadError: { type: String, default: '' },
  // AI 总结任务创建中 (防重复点击)
  summarizing: { type: Boolean, default: false },
  // 创建总结任务失败的错误信息 (免费档每日配额用尽 429 等, 父组件透传)
  summarizeError: { type: String, default: '' },
  // 总结禁用原因 (ADR-0005 + 用户反馈: 已总结过不可再次总结), Home 按当前
  // 解析链接计算传入: '' 可总结 / 'active' 已有进行中任务 / 'retry' 上次
  // 总结失败 (可点击重试) / 'done' 已总结过
  summarizeDisabled: { type: String, default: '' },
})

const emit = defineEmits(['download', 'go-member', 'summarize', 'retry-summarize'])

// 档位倒序 (高清晰度在上, 用户反馈 bugfix/0006): 后端按高度升序排列
const formatsDesc = computed(() => [...props.result.formats].reverse())

// 默认选中最高可用档: 倒序找第一个未锁定档 = 最高档
// (免费取最高免费档, 会员取最高真实档; 最佳画质伪档后端已裁剪)
const selectedFormat = ref(
  (formatsDesc.value.find((f) => !f.locked) ??
    props.result.formats[0])?.format_id ?? '',
)

// 档位列表原地更新时 (会员解锁后 keepOld 重解析, result 引用替换但组件
// 不重建, setup 只初始化一次) 重置默认选中: 保持与非会员一致的设计 —
// 默认选中最高可用档 (解锁后自动切到最高真实档)
watch(
  () => props.result.formats,
  (formats) => {
    const firstUnlocked = [...formats].reverse().find((f) => !f.locked)
    if (firstUnlocked) selectedFormat.value = firstUnlocked.format_id
  },
  { deep: true },
)

// 时长格式化: 秒 → "mm:ss" / "h:mm:ss";
// 无时长返回空串
function formatDuration(seconds) {
  if (seconds == null || Number.isNaN(seconds)) return ''
  const total = Math.max(0, Math.round(seconds))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const mm = String(m).padStart(2, '0')
  const ss = String(s).padStart(2, '0')
  return h > 0 ? `${h}:${mm}:${ss}` : `${m}:${ss}`
}

const durationText = computed(() => formatDuration(props.result.duration))

// 当前选中档位是否锁定: 全部档位锁定 (会员专属视频) 时
// 禁用「开始下载」, 避免点击必然被后端拒绝
const selectedLocked = computed(
  () =>
    props.result.formats.find((f) => f.format_id === selectedFormat.value)?.locked ??
    false,
)

// 档位展示文案: 后端 label 已含清晰度 + 容器
// (如 "1080p MP4"), 此处直接展示
function formatLabel(f) {
  return f.label
}

function handleDownload() {
  if (!selectedFormat.value || props.downloading) return
  emit('download', { url: props.url, formatId: selectedFormat.value })
}

function handleSummarize() {
  if (props.summarizing) return
  // 上次总结失败 (任务级 failed): 走重试流程 (重跑失败子任务, 不新建任务
  // 不扣配额); 其余情况新建总结任务
  if (props.summarizeDisabled === 'retry') {
    emit('retry-summarize', props.url)
  } else {
    emit('summarize', props.url)
  }
}

// ---- 自定义下拉 (用户反馈: 原生 select 展开列表是浏览器渲染, 样式不可控;
// 改为按钮 + 展开列表, 圆角边框 + hover 主题色均可自定义) ----
const dropdownOpen = ref(false)
const dropdownRoot = ref(null)

// 触发器文案: 当前选中档位 (含 🔒 标识) / 无档位占位
const selectedLabel = computed(() => {
  const f = props.result.formats.find((x) => x.format_id === selectedFormat.value)
  return f ? (f.locked ? '🔒 ' : '') + formatLabel(f) : '请选择清晰度'
})

function toggleDropdown() {
  if (!formatsDesc.value.length) return
  dropdownOpen.value = !dropdownOpen.value
}

function selectFormat(f) {
  if (f.locked) return
  selectedFormat.value = f.format_id
  dropdownOpen.value = false
}

// 点击下拉区域外部关闭 (与展开状态解耦, 重复绑定无副作用)
function onDocClick(e) {
  if (dropdownRoot.value && !dropdownRoot.value.contains(e.target)) {
    dropdownOpen.value = false
  }
}

onMounted(() => document.addEventListener('click', onDocClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocClick))
</script>

<template>
  <section class="result fade-up" aria-label="解析结果">
    <div class="result__cover">
      <!-- referrerpolicy: 图床防盗链 (B 站等拒绝第三方 Referer, 403 不显示);
           不发 Referer 头即放行 (见 bugfix/0001) -->
      <img
        v-if="result.cover"
        :src="result.cover"
        :alt="result.title"
        loading="lazy"
        referrerpolicy="no-referrer"
      />
      <div v-else class="result__cover-placeholder">🎬</div>
    </div>

    <div class="result__body">
      <div class="result__meta">
        <span v-if="result.site" class="badge">{{ result.site }}</span>
        <span v-if="durationText" class="badge badge--plain">{{ durationText }}</span>
        <span v-if="result.member_limited" class="badge">🔒 含会员专属档位</span>
        <button
          v-if="result.member_limited"
          class="btn-outline-gradient result__unlock"
          type="button"
          @click="emit('go-member')"
        >
          🔓 解锁会员档位
        </button>
      </div>

      <h2 class="result__title">{{ result.title }}</h2>

      <div class="result__formats">
        <span class="result__formats-label">选择清晰度</span>
        <div ref="dropdownRoot" class="result__select-wrap">
          <button
            type="button"
            class="result__select"
            :class="{ 'is-open': dropdownOpen }"
            :disabled="!formatsDesc.length"
            aria-haspopup="listbox"
            :aria-expanded="dropdownOpen"
            @click="toggleDropdown"
          >
            <span class="result__select-value">{{ selectedLabel }}</span>
            <span class="result__select-arrow" aria-hidden="true">▾</span>
          </button>
          <!-- 展开列表: 倒序渲染, 最高画质在第一位 (bugfix/0006) -->
          <ul v-if="dropdownOpen" class="result__select-menu" role="listbox">
            <li
              v-for="f in formatsDesc"
              :key="f.format_id"
              class="result__select-option"
              :class="{
                'is-locked': f.locked,
                'is-selected': f.format_id === selectedFormat,
              }"
              role="option"
              :aria-selected="f.format_id === selectedFormat"
              @click="selectFormat(f)"
            >
              {{ f.locked ? '🔒 ' : '' }}{{ formatLabel(f) }}
            </li>
          </ul>
        </div>
        <span v-if="result.formats.length === 0" class="result__formats-empty">
          该视频暂无可用档位
        </span>
        <button
          class="btn-gradient result__download"
          :disabled="downloading || !selectedFormat || selectedLocked"
          @click="handleDownload"
        >
          <span v-if="downloading" class="result__spinner" aria-hidden="true"></span>
          {{ downloading ? '创建中…' : '开始下载' }}
        </button>
        <button
          class="btn-outline-gradient result__summarize"
          :disabled="
            summarizing || ['active', 'done'].includes(summarizeDisabled)
          "
          @click="handleSummarize"
        >
          <span v-if="summarizing" class="result__spinner" aria-hidden="true"></span>
          {{
            summarizeDisabled === 'active'
              ? '总结生成中…'
              : summarizeDisabled === 'done'
                ? '已总结过'
                : summarizeDisabled === 'retry'
                  ? '↻ 重试 AI 总结'
                  : summarizing
                    ? '总结中…'
                    : '✨ AI 总结'
          }}
        </button>
        <ErrorAlert :message="downloadError" />
        <ErrorAlert :message="summarizeError" />
      </div>
    </div>
  </section>
</template>

<style scoped>
.result {
  display: flex;
  gap: 24px;
  padding: 20px;
  border-radius: var(--radius);
  background: var(--card);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-card);
  margin-top: 32px;
  /* 展开面板需盖住下方「下载记录」区域: fade-up 动画的 transform 让 .result
     形成 stacking context, 面板 z-index 只在卡片内生效, 无法越过后续兄弟
     .tasks; 卡片自身提升层级即可 (用户反馈: 下拉列表被下载记录盖住) */
  position: relative;
  z-index: 10;
}

/* 封面宽度加大 (用户反馈: 图片展示区域左右长一点) */
.result__cover {
  flex: 0 0 320px;
  border-radius: var(--radius-sm);
  overflow: hidden;
  background: rgba(31, 35, 41, 0.04);
  aspect-ratio: 16 / 9;
}

.result__cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.result__cover-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 40px;
}

.result__body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.result__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.result__title {
  font-size: 20px;
  font-weight: 700;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.result__formats {
  margin-top: auto;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.result__formats-label {
  font-size: 14px;
  color: var(--text-sub);
}

/* 自定义下拉 (用户反馈: 展开面板样式可自定义): 触发器定位上下文 */
.result__select-wrap {
  position: relative;
}

.result__select {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  min-width: 220px;
  height: 42px;
  padding: 0 14px;
  /* 胶囊圆角 (与页面输入框 .qa__input 一致, 用户反馈: 边框圆滑) */
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--bg-deep);
  color: var(--text-main);
  font-size: 14px;
  outline: none;
  cursor: pointer;
  transition:
    border-color 0.2s ease,
    box-shadow 0.2s ease;
}

.result__select:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

/* hover / 展开: 边框用网页主题色浅粉 (用户反馈) */
.result__select:hover,
.result__select.is-open {
  border-color: var(--primary);
}

.result__select:focus-visible {
  box-shadow: 0 0 0 2px rgba(251, 114, 153, 0.25);
}

.result__select-value {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 箭头: 展开时旋转 180° */
.result__select-arrow {
  flex: 0 0 auto;
  color: var(--text-dim);
  font-size: 12px;
  transition: transform 0.2s ease;
}

.result__select.is-open .result__select-arrow {
  transform: rotate(180deg);
}

/* 展开列表: 圆角 + 主题色描边 + 阴影, 项 hover 浅粉底 (用户反馈) */
.result__select-menu {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  right: 0;
  z-index: 10;
  margin: 0;
  padding: 6px;
  list-style: none;
  border-radius: 12px;
  border: 1px solid rgba(251, 114, 153, 0.35);
  background: var(--card);
  box-shadow: 0 8px 24px rgba(31, 35, 41, 0.12);
  max-height: 280px;
  overflow-y: auto;
}

.result__select-option {
  padding: 9px 12px;
  border-radius: 8px;
  font-size: 14px;
  color: var(--text-main);
  cursor: pointer;
  transition:
    background 0.15s ease,
    color 0.15s ease;
}

.result__select-option:hover {
  background: var(--primary-soft);
  color: var(--primary);
}

.result__select-option.is-selected {
  font-weight: 600;
  color: var(--primary);
}

/* 锁定档位: 置灰不可点 (hover 不变色, 与原生 disabled 语义一致) */
.result__select-option.is-locked {
  color: var(--text-dim);
  opacity: 0.7;
  cursor: not-allowed;
}

.result__select-option.is-locked:hover {
  background: transparent;
  color: var(--text-dim);
}

.result__formats-empty {
  font-size: 14px;
  color: var(--text-dim);
}

.result__download {
  height: 42px;
  padding: 0 24px;
  font-size: 14px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  white-space: nowrap;
}

/* AI 总结入口 (与下载按钮并排, 渐变描边区分主次) */
.result__summarize {
  height: 42px;
  padding: 0 24px;
  font-size: 14px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  white-space: nowrap;
}

/* 会员档位解锁引导 (US 35: 免费用户看到锁定档位时获转化路径) */
.result__unlock {
  padding: 7px 16px;
  font-size: 13px;
  margin-left: auto;
}

/* 创建中旋转指示 */
.result__spinner {
  width: 13px;
  height: 13px;
  border: 2px solid rgba(31, 35, 41, 0.3);
  border-top-color: #1f2329;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 640px) {
  .result {
    flex-direction: column;
  }

  .result__cover {
    flex-basis: auto;
  }

  /* 下拉触发器: 外层 wrap 占满剩余宽度 (按钮内部 flex 布局自适应) */
  .result__select-wrap {
    flex: 1;
    min-width: 0;
  }

  .result__select {
    min-width: 0;
    width: 100%;
  }

  .result__download {
    flex: 1;
    justify-content: center;
  }
}
</style>
