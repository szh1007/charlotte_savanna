<script setup>
// 页脚 (T09): 版权免责声明
// 仅个人学习使用 / 不破解 DRM / 封号风险自担 / 尊重版权 (PRD §10.7)
const year = new Date().getFullYear()
</script>

<template>
  <footer class="footer">
    <div class="container footer__inner">
      <div class="footer__brand">
        <span class="footer__logo">🅱️ BilibiliDownloader</span>
        <p class="footer__copy">© {{ year }} BilibiliDownloader · 保留所有权利</p>
      </div>
      <div class="footer__disclaimer">
        <p class="footer__disclaimer-title">免责声明</p>
        <ul class="footer__disclaimer-list">
          <li>本工具仅供个人学习与备份自有内容使用, 请勿用于商业用途</li>
          <li>本工具不破解 DRM 加密, 不绕过平台付费墙, 请勿下载付费或会员专享内容</li>
          <li>使用本工具存在平台封号风险, 由此产生的一切后果由用户自行承担</li>
          <li>请尊重视频版权与平台服务条款, 支持正版内容创作者</li>
        </ul>
      </div>
    </div>
  </footer>
</template>

<style scoped>
.footer {
  margin-top: 72px;
  padding: 36px 0 28px;
  border-top: 1px solid var(--border);
  background: rgba(255, 255, 255, 0.85);
}

.footer__inner {
  display: flex;
  justify-content: space-between;
  gap: 32px;
  flex-wrap: wrap;
}

.footer__logo {
  font-size: 16px;
  font-weight: 700;
  background: var(--gradient);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}

.footer__copy {
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-dim);
}

.footer__disclaimer {
  flex: 1;
  min-width: 260px;
  max-width: 560px;
}

.footer__disclaimer-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--text-sub);
}

.footer__disclaimer-list {
  margin-top: 10px;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.footer__disclaimer-list li {
  position: relative;
  padding-left: 14px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--text-dim);
}

.footer__disclaimer-list li::before {
  content: '';
  position: absolute;
  left: 0;
  top: 8px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--gradient);
}

@media (max-width: 640px) {
  .footer {
    margin-top: 48px;
  }

  .footer__inner {
    flex-direction: column;
    gap: 20px;
  }
}
</style>
