<script setup>
import { onMounted, ref } from 'vue'
import { fetchSites } from '../api/client.js'

// 平台墙 (T09): 彩色卡片网格 (icon + 名称 + 支持格式)
// 数据来自平台接口 /api/sites (引擎支持性过滤 + 全量支持数),
// 卡片粉彩描边 hover 上浮
const sites = ref([])
const total = ref(0)
const loadError = ref('')

onMounted(async () => {
  try {
    const body = await fetchSites()
    sites.value = body.sites
    total.value = body.total
  } catch (e) {
    loadError.value = e.message
  }
})
</script>

<template>
  <section class="sites fade-up" aria-label="支持平台">
    <div class="sites__head">
      <h2 class="sites__title">支持平台</h2>
      <p class="sites__subtitle">
        引擎原生支持 <strong class="sites__total">{{ total || '2000+' }}</strong> 个网站, 以下为精选
      </p>
    </div>

    <p v-if="loadError" class="sites__error" role="alert">
      平台列表加载失败: {{ loadError }}
    </p>

    <ul class="sites__grid">
      <li v-for="s in sites" :key="s.name" class="site-card">
        <span class="site-card__icon" aria-hidden="true">{{ s.icon }}</span>
        <span class="site-card__name">{{ s.name }}</span>
        <span class="site-card__formats">{{ s.formats }}</span>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.sites {
  margin-top: 64px;
}

.sites__head {
  text-align: center;
}

.sites__title {
  font-size: 26px;
  font-weight: 800;
}

.sites__subtitle {
  margin-top: 8px;
  font-size: 14px;
  color: var(--text-sub);
}

.sites__total {
  color: var(--primary);
}

.sites__error {
  margin-top: 14px;
  text-align: center;
  font-size: 13px;
  color: var(--danger);
}

/* 卡片网格: 自动填充, 小屏自然降列 */
.sites__grid {
  margin-top: 28px;
  list-style: none;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 14px;
}

/* 平台卡片: 粉彩描边 + hover 上浮光晕 */
.site-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 22px 12px 18px;
  border-radius: var(--radius);
  background: var(--card);
  border: 1px solid rgba(235, 47, 150, 0.22);
  transition:
    transform 0.25s ease,
    border-color 0.25s ease,
    box-shadow 0.25s ease;
}

.site-card:hover {
  transform: translateY(-5px);
  border-color: rgba(235, 47, 150, 0.6);
  box-shadow: 0 12px 32px rgba(235, 47, 150, 0.18);
}

.site-card__icon {
  font-size: 34px;
  line-height: 1;
}

.site-card__name {
  font-size: 14px;
  font-weight: 600;
}

.site-card__formats {
  font-size: 12px;
  color: var(--text-dim);
  font-family: var(--font-mono);
}
</style>
