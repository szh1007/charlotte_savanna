<script setup>
import { ref } from 'vue'

// Hero 解析区: 大标题 + 链接输入框 + 解析按钮
// + 平台标签云
// 解析动作交由父组件执行 (Home.vue),
// 本组件仅负责输入与加载态
const props = defineProps({
  resolving: { type: Boolean, default: false },
  // 接口级错误 (解析失败提示, 父组件传入)
  apiError: { type: String, default: '' },
})

const emit = defineEmits(['resolve'])

const url = ref('')
// 输入级校验错误 (空链接 / 非 http 前缀)
const error = ref('')

function handleResolve() {
  const trimmed = url.value.trim()
  if (!trimmed) {
    error.value = '请先粘贴视频链接'
    return
  }
  if (!/^https?:\/\//.test(trimmed)) {
    error.value = '链接必须以 http:// 或 https:// 开头'
    return
  }
  error.value = ''
  emit('resolve', trimmed)
}

// 平台标签云: 精选平台静态展示 (PRD §9), 平台墙接口数据见 T09
const tags = [
  '哔哩哔哩',
  '抖音',
  'YouTube',
  '小红书',
  '微博',
  '快手',
  '西瓜视频',
  '腾讯视频',
  '优酷',
  '爱奇艺',
  'Twitch',
  'Vimeo',
]
</script>

<template>
  <section class="hero">
    <!-- 装饰光斑 -->
    <div class="hero__glow hero__glow--1" aria-hidden="true"></div>
    <div class="hero__glow hero__glow--2" aria-hidden="true"></div>

    <div class="container hero__content">
      <h1 class="hero__title">全网视频，<span class="hero__title-accent">一键下载</span></h1>
      <p class="hero__subtitle">支持 2000+ 平台 · 批量下载 · 高清任选</p>

      <form class="hero__form" @submit.prevent="handleResolve">
        <input
          v-model="url"
          class="hero__input"
          type="url"
          inputmode="url"
          placeholder="粘贴视频链接, 如 https://b23.tv/xxxxxx"
          autocomplete="off"
          spellcheck="false"
        />
        <button class="btn-gradient hero__btn" type="submit" :disabled="resolving">
          <span v-if="resolving" class="hero__btn-spinner" aria-hidden="true"></span>
          {{ resolving ? '解析中…' : '开始解析' }}
        </button>
      </form>

      <p v-if="apiError || error" class="hero__error" role="alert">{{ apiError || error }}</p>

      <ul class="hero__tags">
        <li v-for="tag in tags" :key="tag" class="hero__tag">{{ tag }}</li>
      </ul>
    </div>
  </section>
</template>

<style scoped>
.hero {
  position: relative;
  overflow: hidden;
  padding: 88px 0 64px;
  text-align: center;
  background:
    linear-gradient(180deg, rgba(13, 10, 26, 0.4) 0%, rgba(18, 14, 36, 0.6) 100%),
    var(--bg-deep);
}

/* 霓虹光斑 */
.hero__glow {
  position: absolute;
  border-radius: 50%;
  filter: blur(90px);
  pointer-events: none;
}

.hero__glow--1 {
  width: 480px;
  height: 480px;
  top: -180px;
  left: 50%;
  transform: translateX(-50%);
  background: rgba(235, 47, 150, 0.28);
}

.hero__glow--2 {
  width: 420px;
  height: 420px;
  bottom: -220px;
  left: -120px;
  background: rgba(114, 46, 209, 0.3);
}

.hero__content {
  position: relative;
}

.hero__title {
  font-size: clamp(36px, 6vw, 60px);
  font-weight: 800;
  letter-spacing: 1px;
  line-height: 1.2;
}

.hero__title-accent {
  background: var(--gradient);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}

.hero__subtitle {
  margin-top: 14px;
  font-size: clamp(15px, 2vw, 18px);
  color: var(--text-sub);
}

/* 大号输入框 + 解析按钮 */
.hero__form {
  margin: 36px auto 0;
  max-width: 680px;
  display: flex;
  gap: 10px;
  padding: 6px;
  border-radius: 999px;
  background: var(--card);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-card);
}

.hero__input {
  flex: 1;
  min-width: 0;
  height: 52px;
  padding: 0 20px;
  border: none;
  outline: none;
  background: transparent;
  color: var(--text-main);
  font-size: 15px;
  border-radius: 999px;
}

.hero__input::placeholder {
  color: var(--text-dim);
}

.hero__input:focus {
  box-shadow: 0 0 0 2px rgba(235, 47, 150, 0.35) inset;
}

.hero__btn {
  height: 52px;
  padding: 0 32px;
  font-size: 16px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  white-space: nowrap;
}

/* 解析中旋转指示 */
.hero__btn-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid rgba(255, 255, 255, 0.4);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.hero__error {
  margin-top: 14px;
  color: var(--danger);
  font-size: 14px;
}

/* 平台标签云 */
.hero__tags {
  margin-top: 28px;
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 10px;
  list-style: none;
}

.hero__tag {
  padding: 5px 14px;
  border-radius: 999px;
  font-size: 13px;
  color: var(--text-sub);
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid var(--border);
  transition:
    color 0.2s ease,
    border-color 0.2s ease,
    transform 0.2s ease;
}

.hero__tag:hover {
  color: var(--primary);
  border-color: rgba(235, 47, 150, 0.5);
  transform: translateY(-2px);
}

@media (max-width: 640px) {
  .hero {
    padding: 56px 0 40px;
  }

  .hero__form {
    flex-direction: column;
    border-radius: var(--radius);
    padding: 12px;
  }

  .hero__input,
  .hero__btn {
    width: 100%;
    border-radius: 999px;
  }
}
</style>
