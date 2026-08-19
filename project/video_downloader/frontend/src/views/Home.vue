<script setup>
import { ref } from 'vue'
import NavBar from '../components/NavBar.vue'
import HeroSection from '../components/HeroSection.vue'
import ResolveResult from '../components/ResolveResult.vue'
import { resolveUrl } from '../api/client.js'

// 单页布局 (PRD §10): 导航 → Hero → 解析结果 (任务面板/平台墙/会员区/页脚见 T08/T09)
const resolving = ref(false)
const result = ref(null)
const apiError = ref('')

async function handleResolve(url) {
  resolving.value = true
  result.value = null
  apiError.value = ''
  try {
    result.value = await resolveUrl(url)
  } catch (e) {
    apiError.value = e.message
  } finally {
    resolving.value = false
  }
}
</script>

<template>
  <NavBar />
  <HeroSection
    :resolving="resolving"
    :api-error="apiError"
    @resolve="handleResolve"
  />
  <main class="container">
    <ResolveResult v-if="result" :result="result" />
  </main>
</template>
