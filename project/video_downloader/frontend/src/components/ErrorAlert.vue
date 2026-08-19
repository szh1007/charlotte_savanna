<script setup>
// 统一错误提示条 (bugfix/0007): 解析/下载/清除/解锁等失败信息的
// 美观化展示, 替代各处裸红字文本. 空消息不渲染 (调用方无需额外 v-if).
// 间距由调用方控制 (父组件 class 作用于根节点)
defineProps({
  message: { type: String, default: '' },
})
</script>

<template>
  <p v-if="message" class="error-alert" role="alert">
    <span class="error-alert__icon" aria-hidden="true">⚠️</span>
    <span>{{ message }}</span>
  </p>
</template>

<style scoped>
.error-alert {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 14px;
  border-radius: var(--radius-sm);
  background: rgba(255, 77, 79, 0.1);
  border: 1px solid rgba(255, 77, 79, 0.35);
  color: var(--danger);
  font-size: 13px;
  line-height: 1.5;
  animation: alert-in 0.3s ease both;
}

.error-alert__icon {
  flex: 0 0 auto;
  line-height: 1.4;
}

@keyframes alert-in {
  from {
    opacity: 0;
    transform: translateY(-4px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>
