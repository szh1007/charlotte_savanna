<script setup>
// AI 总结字幕来源全局设置 (ADR-0006):
// 官方字幕 (快路径, 无字幕自动回退模型生成) / 模型生成 (缓存优先)
// + 语音转写模型状态 (缺失→下载按钮, 下载中→进度, 就绪→绿标)
// 选择持久化到 localStorage, 创建总结任务时随请求携带
import { computed } from 'vue'

const props = defineProps({
  // 当前选择的字幕来源: official | model (Home 持久化, 默认 official)
  modelValue: { type: String, default: 'official' },
  // 模型状态快照 (Home 持有, SSE model-update 事件更新):
  // {status: missing|downloading|ready, progress: 0~100, has_official_subtitle}
  modelStatus: { type: Object, default: () => ({}) },
  // 手动下载请求中 (防重复点击)
  modelDownloading: { type: Boolean, default: false },
  // 手动下载失败错误信息 (父组件透传)
  modelError: { type: String, default: '' },
})

const emit = defineEmits(['update:modelValue', 'download-model'])

// v-model 双向绑定: props 不可直接写, 经 computed 转发 emit
const selected = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const isMissing = computed(() => props.modelStatus.status === 'missing')
const isDownloading = computed(() => props.modelStatus.status === 'downloading')
const isReady = computed(() => props.modelStatus.status === 'ready')

// 进度展示: 下载中显示当前百分比, 完成显示 100%
const progressText = computed(() => `${Math.round(props.modelStatus.progress ?? 0)}%`)

// 官方字幕可用性提示: 未配置 B 站 Cookie 时官方字幕快路径不可用
// (后端 has_official_subtitle=false), 提示用户可切换模型生成
const cookieMissing = computed(
  () =>
    selected.value === 'official' &&
    props.modelStatus.has_official_subtitle === false,
)

function handleDownload() {
  if (props.modelDownloading || props.modelStatus.status !== 'missing') return
  emit('download-model')
}
</script>

<template>
  <section class="sub-source fade-up" aria-label="AI 总结字幕来源设置">
    <div class="sub-source__head">
      <span class="sub-source__title">✨ AI 总结字幕来源</span>
      <span class="sub-source__hint">创建总结任务时按此设置获取字幕</span>
    </div>

    <div class="sub-source__row">
      <div class="sub-source__options" role="radiogroup" aria-label="字幕来源">
        <label class="sub-source__option" :class="{ 'is-active': selected === 'official' }">
          <input v-model="selected" type="radio" name="subtitle-source" value="official" />
          <span class="sub-source__option-body">
            <span class="sub-source__option-name">官方字幕</span>
            <span class="sub-source__option-desc">B 站自带字幕, 秒级获取</span>
          </span>
        </label>
        <label class="sub-source__option" :class="{ 'is-active': selected === 'model' }">
          <input v-model="selected" type="radio" name="subtitle-source" value="model" />
          <span class="sub-source__option-body">
            <span class="sub-source__option-name">模型生成</span>
            <span class="sub-source__option-desc">AI 转写语音, 缓存复用</span>
          </span>
        </label>
      </div>

      <!-- 模型状态区: 缺失→下载引导 / 下载中→进度 / 就绪→绿标 -->
      <div class="sub-source__model">
        <template v-if="isReady">
          <span class="sub-source__model-state sub-source__model-state--ready">
            ✓ 语音模型已就绪
          </span>
        </template>
        <template v-else-if="isDownloading">
          <div class="sub-source__model-progress">
            <div class="sub-source__progress-track">
              <div
                class="sub-source__progress-bar"
                :style="{ width: progressText }"
              ></div>
            </div>
            <span class="sub-source__model-state">{{ progressText }}</span>
          </div>
          <span class="sub-source__model-hint">模型下载中, 不影响其他操作</span>
        </template>
        <template v-else>
          <button
            class="btn-outline-gradient sub-source__model-btn"
            type="button"
            :disabled="modelDownloading"
            @click="handleDownload"
          >
            <span v-if="modelDownloading" class="sub-source__spinner" aria-hidden="true"></span>
            {{ modelDownloading ? '下载中…' : '⬇ 下载语音模型 (约 1GB)' }}
          </button>
          <span v-if="modelError" class="sub-source__model-error">{{ modelError }}</span>
        </template>
      </div>
    </div>

    <!-- 未配置 Cookie 提示: 官方字幕可能获取失败, 引导切换模型生成 -->
    <p v-if="cookieMissing" class="sub-source__warn">
      ⚠ 服务端未配置 B 站 Cookie, 官方字幕可能获取失败 (失败时将自动切换模型生成)
    </p>
  </section>
</template>

<style scoped>
.sub-source {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 24px;
  padding: 16px 20px;
  border-radius: var(--radius);
  background: var(--card);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-card);
}

.sub-source__head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}

.sub-source__title {
  font-size: 15px;
  font-weight: 700;
}

.sub-source__hint {
  font-size: 13px;
  color: var(--text-dim);
}

.sub-source__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  flex-wrap: wrap;
}

/* 双选项 radio: 卡片式选择 */
.sub-source__options {
  display: flex;
  gap: 10px;
}

.sub-source__option {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--bg-deep);
  cursor: pointer;
  transition:
    border-color 0.2s ease,
    background 0.2s ease;
}

.sub-source__option.is-active {
  border-color: var(--primary);
  background: var(--primary-soft);
}

.sub-source__option input {
  accent-color: var(--primary);
}

.sub-source__option-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.sub-source__option-name {
  font-size: 14px;
  font-weight: 600;
}

.sub-source__option-desc {
  font-size: 12px;
  color: var(--text-dim);
}

/* 模型状态区 */
.sub-source__model {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 6px;
  min-width: 200px;
}

.sub-source__model-state {
  font-size: 13px;
  color: var(--text-sub);
}

.sub-source__model-state--ready {
  color: var(--success);
  font-weight: 600;
}

.sub-source__model-progress {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
}

.sub-source__progress-track {
  flex: 1;
  height: 8px;
  border-radius: 999px;
  background: rgba(31, 35, 41, 0.08);
  overflow: hidden;
}

.sub-source__progress-bar {
  height: 100%;
  border-radius: 999px;
  background: var(--gradient);
  transition: width 0.3s ease;
}

.sub-source__model-hint {
  font-size: 12px;
  color: var(--text-dim);
}

.sub-source__model-btn {
  height: 36px;
  padding: 0 18px;
  font-size: 13px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}

.sub-source__model-error {
  font-size: 12px;
  color: var(--danger);
}

/* Cookie 缺失提示 */
.sub-source__warn {
  font-size: 13px;
  color: var(--warning);
  line-height: 1.5;
}

/* 下载中旋转指示 */
.sub-source__spinner {
  width: 12px;
  height: 12px;
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
  .sub-source__row {
    flex-direction: column;
    align-items: stretch;
  }

  .sub-source__options {
    flex-direction: column;
  }

  .sub-source__model {
    align-items: stretch;
  }
}
</style>
