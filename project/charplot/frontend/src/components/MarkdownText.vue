<script setup lang="ts">
// 轻量 markdown 渲染器 (Issue 13): 零依赖, 白名单语法.
// 支持 LLM 状态总结的受控输出 (prompt 约束): ## / ### 标题, **粗体**,
// - 无序列表, 1. 有序列表, 段落. 先全量 HTML 转义再替换语法标记, 杜绝
// XSS (LLM 输出作为 HTML 注入 v-html 前的唯一安全边界).
// 配色由调用方 :deep() 覆盖 (主题令牌在业务侧, 组件保持中性).
import { computed } from 'vue'

const props = defineProps<{ text: string }>()

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

/** 行内语法: **粗体** → <strong> (转义后处理, 标记字符不在转义集合). */
function inline(s: string): string {
  return s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
}

/** 块级渲染: 标题 / 列表 (连续项合并) / 段落, 空行分段. */
function renderBlocks(escaped: string): string {
  const out: string[] = []
  let items: string[] = []
  let listTag: 'ul' | 'ol' = 'ul'

  const flush = () => {
    if (items.length > 0) {
      out.push(`<${listTag}><li>${items.join('</li><li>')}</li></${listTag}>`)
      items = []
    }
  }

  for (const raw of escaped.split('\n')) {
    const line = raw.trim()
    if (!line) {
      flush()
      continue
    }
    if (line.startsWith('## ')) {
      flush()
      out.push(`<h3>${inline(line.slice(3))}</h3>`)
      continue
    }
    if (line.startsWith('### ')) {
      flush()
      out.push(`<h4>${inline(line.slice(4))}</h4>`)
      continue
    }
    const isUl = line.startsWith('- ')
    const isOl = /^\d+\.\s/.test(line)
    if (isUl || isOl) {
      const tag = isUl ? 'ul' : 'ol'
      if (listTag !== tag) flush()
      listTag = tag
      items.push(inline(line.replace(/^(?:-\s|\d+\.\s)/, '')))
      continue
    }
    flush()
    out.push(`<p>${inline(line)}</p>`)
  }
  flush()
  return out.join('')
}

const html = computed(() => renderBlocks(escapeHtml(props.text)))
</script>

<template>
  <!-- v-html 安全前提: 输入已全量转义, 仅白名单语法标签保留 -->
  <div class="md" v-html="html" />
</template>

<style scoped>
/* 中性排版: 配色由调用方主题令牌覆盖 */
.md {
  font-size: 14px;
  line-height: 1.8;
  color: var(--cp-ink);
}

.md :deep(h3),
.md :deep(h4) {
  margin: 0 0 10px;
  font-weight: 700;
  color: var(--cp-ink);
}

.md :deep(h3) {
  font-size: 15px;
}

.md :deep(h4) {
  font-size: 14px;
}

.md :deep(p) {
  margin: 0 0 8px;
}

.md :deep(ul),
.md :deep(ol) {
  margin: 0 0 8px;
  padding-left: 20px;
}

.md :deep(li) {
  margin: 2px 0;
}

.md :deep(strong) {
  font-weight: 700;
  color: var(--cp-ink);
}
</style>
