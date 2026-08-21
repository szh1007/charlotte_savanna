<script setup>
// 功能亮点区 (首页营销): 下载 + AI 理解 6 大卖点, 文案与实际能力对齐
// (AI 总结 / 思维导图 / 问答见 SummaryPanel, 字幕来源见 SubtitleSourceBar,
//  清晰度与并发配额见 MemberSection 对比表)
const FEATURES = [
  {
    icon: '✨',
    title: 'AI 视频总结',
    desc: '自动生成视频概述、章节时间线、核心要点与结论, 支持 Markdown / PDF 一键导出',
  },
  {
    icon: '📝',
    title: '字幕转写',
    desc: '官方字幕直取, 无字幕视频 AI 语音转写 + 润色, 导出 TXT / SRT / VTT',
  },
  {
    icon: '🧠',
    title: '思维导图',
    desc: '基于总结自动生成知识导图, 缩放 / 平移 / 全屏, 导出 PNG / SVG / PDF',
  },
  {
    icon: '💬',
    title: 'AI 问答',
    desc: '基于转录与总结上下文, 针对视频内容自由提问, 答案流式输出',
  },
  {
    icon: '🎬',
    title: '高清任选',
    desc: '免费最高 720p, 会员解锁 1080p / 4K 与最佳画质',
  },
  {
    icon: '⚡',
    title: '批量下载',
    desc: '免费 5 个批量队列, 会员 50 个队列 + 3 并发任务',
  },
]
</script>

<template>
  <section class="features section">
    <div class="container">
      <h2 class="features__title">下载之外, 更懂视频</h2>
      <p class="features__subtitle">不止下载 — AI 帮你总结、转写、画图、答疑</p>

      <ul class="features__grid">
        <li v-for="f in FEATURES" :key="f.title" class="feature-card">
          <span class="feature-card__icon" aria-hidden="true">{{ f.icon }}</span>
          <h3 class="feature-card__title">{{ f.title }}</h3>
          <p class="feature-card__desc">{{ f.desc }}</p>
        </li>
      </ul>
    </div>
  </section>
</template>

<style scoped>
.features {
  padding-top: 0; /* Hero 自带底部间距, 区块直接承接 */
}

.features__title {
  text-align: center;
  font-size: clamp(24px, 3.5vw, 32px);
  font-weight: 800;
  letter-spacing: 0.5px;
}

.features__subtitle {
  margin-top: 10px;
  text-align: center;
  font-size: 14px;
  color: var(--text-sub);
}

.features__grid {
  margin-top: 32px;
  list-style: none;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
}

/* 白卡: 圆角描边 + 悬浮上浮 (与解析结果卡同风格) */
.feature-card {
  padding: 24px 22px;
  border-radius: var(--radius);
  background: var(--card);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-card);
  transition:
    transform 0.25s ease,
    box-shadow 0.25s ease;
}

.feature-card:hover {
  transform: translateY(-4px);
  box-shadow: 0 12px 36px rgba(0, 174, 236, 0.18);
}

.feature-card__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 46px;
  height: 46px;
  border-radius: 14px;
  font-size: 22px;
  background: var(--primary-soft);
}

.feature-card:nth-child(even) .feature-card__icon {
  background: rgba(0, 174, 236, 0.12);
}

.feature-card__title {
  margin-top: 14px;
  font-size: 16px;
  font-weight: 700;
}

.feature-card__desc {
  margin-top: 8px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--text-sub);
}

@media (max-width: 900px) {
  .features__grid {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (max-width: 640px) {
  .features__grid {
    grid-template-columns: 1fr;
  }
}
</style>
