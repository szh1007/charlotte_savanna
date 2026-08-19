<script setup>
import { computed, ref } from 'vue'

// 解析结果卡: 封面 + 标题 + 平台徽章 + 时长 + 清晰度下拉 (锁定档带 🔒)
// 「开始下载」动作属于 T08, 本组件先完成结果展示与档位选择
const props = defineProps({
  result: { type: Object, required: true }, // ResolveResponse: task_id/title/cover/duration/site/formats/member_limited
})

const selectedFormat = ref(props.result.formats[0]?.format_id ?? '')

// 时长格式化: 秒 → "mm:ss" / "h:mm:ss"; 无时长返回空串
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

// 清晰度档位展示文案: "1080p · MP4" (高度未知时仅容器)
function formatLabel(f) {
  const quality = f.label
  return `${quality} · ${f.ext.toUpperCase()}`
}
</script>

<template>
  <section class="result fade-up" aria-label="解析结果">
    <div class="result__cover">
      <img v-if="result.cover" :src="result.cover" :alt="result.title" loading="lazy" />
      <div v-else class="result__cover-placeholder">🎬</div>
    </div>

    <div class="result__body">
      <div class="result__meta">
        <span v-if="result.site" class="badge">{{ result.site }}</span>
        <span v-if="durationText" class="badge badge--plain">{{ durationText }}</span>
        <span v-if="result.member_limited" class="badge">🔒 含会员专属档位</span>
      </div>

      <h2 class="result__title">{{ result.title }}</h2>

      <div class="result__formats">
        <span class="result__formats-label">选择清晰度</span>
        <select v-model="selectedFormat" class="result__select">
          <option
            v-for="f in result.formats"
            :key="f.format_id"
            :value="f.format_id"
            :disabled="f.locked"
          >
            {{ f.locked ? '🔒 ' : '' }}{{ formatLabel(f) }}
          </option>
        </select>
        <span v-if="result.formats.length === 0" class="result__formats-empty">
          该视频暂无可用档位
        </span>
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
}

.result__cover {
  flex: 0 0 240px;
  border-radius: var(--radius-sm);
  overflow: hidden;
  background: rgba(255, 255, 255, 0.04);
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

.result__select {
  min-width: 220px;
  height: 42px;
  padding: 0 14px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--bg-deep);
  color: var(--text-main);
  font-size: 14px;
  outline: none;
  cursor: pointer;
}

.result__select:focus {
  box-shadow: 0 0 0 2px rgba(235, 47, 150, 0.35);
}

.result__formats-empty {
  font-size: 14px;
  color: var(--text-dim);
}

@media (max-width: 640px) {
  .result {
    flex-direction: column;
  }

  .result__cover {
    flex-basis: auto;
  }

  .result__select {
    flex: 1;
    min-width: 0;
  }
}
</style>
