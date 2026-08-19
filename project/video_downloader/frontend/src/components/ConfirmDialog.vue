<script setup>
// 自定义确认弹窗 (Teleport 到 body): 替代浏览器原生 confirm/alert (bugfix/0006).
// 两种模式:
// - 确认模式 (默认): 确认/取消 双按钮, 危险操作确认按钮红色
// - 提示模式 (hide-cancel): 仅「知道了」按钮, 用于操作失败等提示
import { watch } from 'vue'

const props = defineProps({
  visible: { type: Boolean, default: false },
  title: { type: String, default: '确认操作' },
  message: { type: String, default: '' },
  // 确认按钮文案 (提示模式固定「知道了」)
  confirmText: { type: String, default: '确认' },
  // 危险操作: 确认按钮红色渐变
  danger: { type: Boolean, default: false },
  // 提示模式: 隐藏取消按钮, 确认按钮仅关闭弹窗
  hideCancel: { type: Boolean, default: false },
})

const emit = defineEmits(['update:visible', 'confirm'])

function close() {
  emit('update:visible', false)
}

function onConfirm() {
  emit('confirm')
  close()
}

// Esc 关闭: 打开时挂监听, 关闭/卸载时移除
watch(
  () => props.visible,
  (v) => {
    if (v) document.addEventListener('keydown', onKeydown)
    else document.removeEventListener('keydown', onKeydown)
  },
)

function onKeydown(e) {
  if (e.key === 'Escape') close()
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="visible"
      class="confirm-overlay"
      @click.self="close"
      @keydown.esc="close"
    >
      <div class="confirm-dialog" role="dialog" aria-modal="true">
        <h3 class="confirm-dialog__title">{{ title }}</h3>
        <p class="confirm-dialog__message">{{ message }}</p>
        <div class="confirm-dialog__actions">
          <button
            v-if="!hideCancel"
            class="btn-outline-gradient confirm-dialog__btn"
            type="button"
            @click="close"
          >
            取消
          </button>
          <button
            class="btn-gradient confirm-dialog__btn"
            :class="{ 'confirm-dialog__btn--danger': danger }"
            type="button"
            @click="onConfirm"
          >
            {{ hideCancel ? '知道了' : confirmText }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.confirm-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: rgba(10, 6, 20, 0.6);
  backdrop-filter: blur(2px);
}

.confirm-dialog {
  width: 100%;
  max-width: 400px;
  padding: 24px;
  border-radius: var(--radius);
  background: var(--card);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-card);
}

.confirm-dialog__title {
  font-size: 16px;
  font-weight: 700;
}

.confirm-dialog__message {
  margin-top: 10px;
  font-size: 14px;
  line-height: 1.6;
  color: var(--text-sub);
  word-break: break-word;
}

.confirm-dialog__actions {
  margin-top: 20px;
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.confirm-dialog__btn {
  padding: 8px 20px;
  font-size: 13px;
}

/* 危险操作确认: 红色渐变 (清除记录不可恢复) */
.confirm-dialog__btn--danger {
  background: linear-gradient(135deg, #ff4d4f, #d9363e);
}

.confirm-dialog__btn--danger:hover {
  background: linear-gradient(135deg, #ff7875, #ff4d4f);
}
</style>
