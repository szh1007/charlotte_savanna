<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Transformer } from 'markmap-lib'
import { Markmap } from 'markmap-view'
import { zoomTransform } from 'd3'

// 思维导图组件: markmap (d3 树形布局 + 曲线分支) 渲染展示 (用户反馈:
// mind-elixir 直角折线不美观, 换 markmap 现代简约风格).
// 数据 {title, chapters:[{start,end,title,points}]} → markdown 层级
// (# 根 / ## 章节 / - 要点) → Transformer 解析 → Markmap 渲染.
// 只读展示: markmap 无编辑模式, 内置缩放/平移/节点折叠 (SDK 处理).
// 导出: 克隆渲染 SVG (foreignObject 节点) 序列化 → SVG 文件;
// SVG → canvas → PNG; PNG 进打印窗口 → PDF (与旧实现同模式).
const props = defineProps({
  title: { type: String, default: '' },
  // 章节: [{start, end, title, points: [string]}]
  chapters: { type: Array, default: () => [] },
})

// 导出画布四周留白 (px, 布局 rect 外扩)
const EXPORT_PAD = 40

// 秒 → mm:ss (章节节点时间前缀, 与总结页时间线一致)
function fmt(s) {
  const t = Math.max(0, Math.round(s || 0))
  return `${String(Math.floor(t / 60)).padStart(2, '0')}:${String(t % 60).padStart(2, '0')}`
}

// 展示/导出状态
const svgEl = ref(null)
const zoomPct = ref(100) // 当前缩放百分比 (工具条显示)
const errMsg = ref('') // 导出错误提示 (工具条下方, 5 秒后自动消失)
let mm = null // Markmap 实例
let transformer = null
let errTimer = null

function notifyErr(msg) {
  errMsg.value = msg
  clearTimeout(errTimer)
  errTimer = setTimeout(() => (errMsg.value = ''), 5000)
}

// chapters → markdown (markmap 解析层级: # 根 / ## 章节 / - 要点;
// 标题/要点内换行压成空格, 避免破坏 markdown 结构)
function toMarkdown() {
  const lines = [`# ${(props.title || '视频').replace(/\s+/g, ' ')}`]
  for (const ch of props.chapters || []) {
    const label = `${fmt(ch.start)} ${ch.title}`.replace(/\s+/g, ' ').trim()
    lines.push(`## ${label}`)
    for (const p of ch.points || []) lines.push(`- ${String(p).replace(/\s+/g, ' ')}`)
  }
  return lines.join('\n')
}

function readCssVar(name, fb) {
  const s = getComputedStyle(document.documentElement)
  return s.getPropertyValue(name).trim() || fb
}

async function render() {
  if (!svgEl.value) return
  transformer ??= new Transformer()
  const { root } = transformer.transform(toMarkdown())
  if (!mm) {
    mm = new Markmap(svgEl.value, {
      duration: 300, // 折叠/展开过渡时长
      initialExpandLevel: 3, // 三层全展开 (根/章节/要点)
      maxWidth: 340, // 要点长文本自动换行
      spacingHorizontal: 110,
      spacingVertical: 12,
      colorFreezeLevel: 2, // 要点沿用章节色, 两色分层不杂乱
      color: (node) => {
        // 章节粉 / 要点蓝 (站点主题色), 根节点沿用 markmap 默认深色块
        const palette = [
          readCssVar('--primary', '#fb7299'),
          readCssVar('--blue', '#00aeec'),
        ]
        return palette[(node.state.depth - 1) % palette.length]
      },
    })
    // 滚轮/拖拽缩放由 SDK 内部 d3 zoom 处理 (markmap 无缩放回调),
    // 复用其公开 zoom 行为实例挂监听: 滚轮/按钮/适应缩放统一同步左上角百分比
    mm.zoom.on('zoom.vue-sync', syncZoom)
  }
  await mm.setData(root)
  mm.fit()
  syncZoom()
}

function syncZoom() {
  if (!svgEl.value || !mm) return
  zoomPct.value = Math.round(zoomTransform(svgEl.value).k * 100)
}

// ---- 导出: 克隆渲染 SVG, 缩放态归位到布局坐标 (去 scale, 平移留白) ----
// markmap DOM: svg > (style, g[zoom 层]) ; 节点为 foreignObject 内嵌 HTML,
// 克隆序列化后 style/命名空间随行, SVG 文件与 canvas 渲染均正常
function buildExportSvg() {
  const clone = svgEl.value.cloneNode(true)
  const rect = mm.state.rect // 布局包围盒 (用户空间, 不受缩放影响)
  const w = rect.x2 - rect.x1 + EXPORT_PAD * 2
  const h = rect.y2 - rect.y1 + EXPORT_PAD * 2
  const zoomLayer = clone.querySelector('g') // 第一个 g = zoom 层 (handleZoom 设 transform)
  if (zoomLayer) {
    zoomLayer.setAttribute(
      'transform',
      `translate(${EXPORT_PAD - rect.x1}, ${EXPORT_PAD - rect.y1})`,
    )
  }
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
  clone.setAttribute('width', w)
  clone.setAttribute('height', h)
  clone.setAttribute('viewBox', `0 0 ${w} ${h}`)
  // 白底 (与画布展示一致; 原 svg 透明背景, 导出文件/PNG 需要底)
  const bg = document.createElementNS('http://www.w3.org/2000/svg', 'rect')
  bg.setAttribute('width', w)
  bg.setAttribute('height', h)
  bg.setAttribute('fill', '#ffffff')
  clone.insertBefore(bg, clone.firstChild)
  return { svg: new XMLSerializer().serializeToString(clone), width: w, height: h }
}

function exportSvg() {
  const { svg } = buildExportSvg()
  const blob = new Blob([svg], { type: 'image/svg+xml' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  const safe = String(props.title || 'video').replace(/[\\/:*?"<>|]/g, '_').slice(0, 40)
  a.download = `mindmap_${safe}.svg`
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000) // 下载已触发, 稍后释放 blob
}

// SVG → PNG Blob (foreignObject 由浏览器原生渲染, 白底合成).
// bugfix: 不用 toDataURL 下载 — 大图 data URL 超出浏览器下载上限 (Chrome ~2MB)
// 会静默失败; toBlob 直接产出二进制无此限制
function svgToPngBlob() {
  return new Promise((resolve, reject) => {
    const { svg, width: w, height: h } = buildExportSvg()
    const img = new Image()
    const url = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' }))
    img.onload = () => {
      const canvas = document.createElement('canvas')
      canvas.width = w
      canvas.height = h
      const ctx = canvas.getContext('2d')
      ctx.fillStyle = '#ffffff'
      ctx.fillRect(0, 0, w, h) // 防边缘锯齿露白 (SVG 已含白底 rect, 双保险)
      ctx.drawImage(img, 0, 0, w, h)
      URL.revokeObjectURL(url)
      canvas.toBlob(
        (blob) => (blob ? resolve(blob) : reject(new Error('PNG 编码失败'))),
        'image/png',
      )
    }
    img.onerror = () => {
      URL.revokeObjectURL(url)
      reject(new Error('思维导图 PNG 渲染失败'))
    }
    img.src = url
  })
}

async function exportPng() {
  try {
    const blob = await svgToPngBlob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    const safe = String(props.title || 'video').replace(/[\\/:*?"<>|]/g, '_').slice(0, 40)
    a.download = `mindmap_${safe}.png`
    // bugfix: 未挂载的 <a>.click() 在 Firefox 不触发下载, 先挂载再移除
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (e) {
    notifyErr(e?.message || 'PNG 导出失败')
  }
}

async function exportPdf() {
  try {
    const blob = await svgToPngBlob()
    const url = URL.createObjectURL(blob)
    // PDF: 新窗口内嵌图片 + window.print(), 用户选择「另存为 PDF」
    // (点击事件链内 window.open, 避免浏览器弹窗拦截)
    const win = window.open('', '_blank')
    if (!win) return
    win.document.write(
      `<html><head><title>思维导图: ${String(props.title || '视频').replace(/[<>&"]/g, '')}</title></head>` +
        `<body style="margin:0;display:flex;justify-content:center">` +
        `<img src="${url}" style="max-width:100%;height:auto"/>` +
        `<script>window.onload=()=>window.print()<\/script></body></html>`,
    )
    win.document.close()
  } catch (e) {
    notifyErr(e?.message || 'PDF 导出失败')
  }
}

// ---- 缩放 (锚点 = 容器中心, 变换由 SDK 处理) ----
function zoomBy(factor) {
  mm?.rescale(factor)
  syncZoom()
}

function zoomReset() {
  mm?.fit()
  syncZoom()
}

// ---- 全屏 (包含工具条, 便于导出操作) ----
const rootEl = ref(null)
const isFullscreen = ref(false)

function toggleFullscreen() {
  if (document.fullscreenElement) {
    document.exitFullscreen()
  } else {
    rootEl.value.requestFullscreen?.()
  }
}

function onFsChange() {
  isFullscreen.value = !!document.fullscreenElement
}

// 数据变化 → 重渲染 (SSE 拉取后 chapters 定型, 通常仅触发一次;
// 组件挂载于 mindmap tab 激活时 (SummaryPanel v-if), 容器可见尺寸正常)
watch(
  () => props.chapters,
  () => {
    if (mm) render()
  },
  { deep: true },
)

onMounted(() => {
  render()
  document.addEventListener('fullscreenchange', onFsChange)
})

onBeforeUnmount(() => {
  clearTimeout(errTimer)
  mm?.destroy()
  document.removeEventListener('fullscreenchange', onFsChange)
})
</script>

<template>
  <div ref="rootEl" class="mindmap-root">
    <div class="mindmap-toolbar">
      <div class="mindmap-zoom">
        <button class="tool-btn" type="button" :disabled="zoomPct <= 50" @click="zoomBy(1 / 1.2)">
          −
        </button>
        <span class="mindmap-zoom__pct">{{ zoomPct }}%</span>
        <button class="tool-btn" type="button" :disabled="zoomPct >= 300" @click="zoomBy(1.2)">
          ＋
        </button>
        <button class="tool-btn" type="button" @click="zoomReset">适应</button>
      </div>
      <span class="mindmap-toolbar__spacer"></span>
      <button class="tool-btn" type="button" @click="toggleFullscreen">
        {{ isFullscreen ? '退出全屏' : '⛶ 全屏' }}
      </button>
      <button class="tool-btn" type="button" @click="exportPdf">⬇ 导出 PDF</button>
      <button class="tool-btn" type="button" @click="exportPng">⬇ PNG</button>
      <button class="tool-btn" type="button" @click="exportSvg">⬇ SVG</button>
    </div>
    <p v-if="errMsg" class="mindmap-error">{{ errMsg }}</p>
    <!-- markmap 渲染容器: 定宽定高 (SDK 内部处理缩放/平移/折叠) -->
    <div class="mindmap-box">
      <svg ref="svgEl" class="mindmap-svg"></svg>
    </div>
  </div>
</template>

<style scoped>
.mindmap-root {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0; /* grid 子项防溢出 (思维导图曾撑破右栏边框) */
}

/* 全屏时: 工具条置顶, 画布区撑满 */
.mindmap-root:fullscreen {
  padding: 20px;
  background: #fff;
}

.mindmap-root:fullscreen .mindmap-box {
  height: calc(100vh - 90px);
}

.mindmap-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.mindmap-zoom {
  display: flex;
  align-items: center;
  gap: 6px;
}

.mindmap-zoom__pct {
  min-width: 44px;
  text-align: center;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-sub);
}

.mindmap-toolbar__spacer {
  flex: 1;
}

/* 导出失败提示 (工具条下方红字, 5 秒自动消失) */
.mindmap-error {
  margin: 0;
  font-size: 12px;
  color: var(--danger, #e74c3c);
}

.tool-btn {
  padding: 5px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: #fff;
  font-size: 12px;
  color: var(--text-sub);
  cursor: pointer;
  transition:
    color 0.2s ease,
    border-color 0.2s ease;
}

.tool-btn:hover:not(:disabled) {
  color: var(--primary);
  border-color: rgba(251, 114, 153, 0.45);
}

.tool-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* SDK 渲染容器: 固定高度 (内部交互由 SDK 处理), 白底与导出图一致 */
.mindmap-box {
  width: 100%;
  height: 460px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: #fff;
  overflow: hidden;
}

/* markmap SVG 撑满容器 (viewBox 由 SDK 管理) */
.mindmap-svg {
  display: block;
  width: 100%;
  height: 100%;
}
</style>
