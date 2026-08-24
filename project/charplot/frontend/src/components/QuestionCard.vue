<script setup lang="ts">
// 题目卡 (Issue 05, PRD D-3): 选择 / 判断 / 填空三种题型交互.
// 选择 = 选项按钮 (单选), 判断 = 对/错大按钮, 填空 = 输入框 + 提交.
// 提交后禁用 (反馈态由 QuizView 接管), 选项在答错后标出正确答案帮助学习.
// Issue 14: 底部低调「题目有问题?」反馈入口 (可选原因, 去重后展示已反馈态).
import { computed, ref, watch } from 'vue'
import type { FlagReason, Question } from '@/api/client'

const props = defineProps<{
  question: Question
  /** 反馈态: 已提交, 禁用交互 (由 QuizView 在判分后置位). */
  submitted?: boolean
  /** 判分结果: 反馈态下所选选项用琥珀描边提示 (非红色叉). */
  isCorrect?: boolean
  /** 当前用户已反馈过此题 (后端持久化, 重进恢复已反馈态). */
  flagged?: boolean
}>()

const emit = defineEmits<{
  submit: [answer: number[] | string[]]
  flag: [reason: FlagReason | '']
}>()

const selected = ref<number | null>(null)
const judgeChoice = ref<string | null>(null)
const fillText = ref('')

// 换题时重置作答状态
watch(
  () => props.question.id,
  () => {
    selected.value = null
    judgeChoice.value = null
    fillText.value = ''
  },
)

const canSubmit = computed(() => {
  if (props.question.question_type === 'choice') return selected.value !== null
  if (props.question.question_type === 'judge') return judgeChoice.value !== null
  return fillText.value.trim().length > 0
})

function submit() {
  if (!canSubmit.value || props.submitted) return
  if (props.question.question_type === 'choice' && selected.value !== null) {
    emit('submit', [selected.value])
  } else if (props.question.question_type === 'judge' && judgeChoice.value) {
    emit('submit', [judgeChoice.value])
  } else {
    emit('submit', [fillText.value.trim()])
  }
}

/** 反馈态选项样式: 答错时用户所选选项用琥珀描边 (温和提示, 非红色叉). */
function optionClass(index: number) {
  return {
    'is-selected': selected.value === index,
    'is-wrong': selected.value === index && !props.isCorrect,
  }
}

// ---- 题目反馈 (Issue 14): 入口低调, 原因可选, 去重后展示已反馈态 ----

/** 可选原因 (与后端 choices 一致), 不选 = 仅标记无原因. */
const FLAG_REASONS: { value: FlagReason; label: string }[] = [
  { value: 'answer_error', label: '答案有误' },
  { value: 'content_error', label: '题目内容有误' },
  { value: 'explanation_error', label: '讲解有误' },
  { value: 'other', label: '其他' },
]

const flagPanelOpen = ref(false)
const flagReason = ref<FlagReason | ''>('')
/** 已反馈 (本地提交后置位; 重进关卡由 props.flagged 恢复). */
const flagged = ref(props.flagged ?? false)

watch(
  () => props.flagged,
  (v) => {
    if (v) flagged.value = true
  },
)

function toggleFlagPanel() {
  if (flagged.value) return
  flagPanelOpen.value = !flagPanelOpen.value
}

function selectFlagReason(value: FlagReason) {
  flagReason.value = flagReason.value === value ? '' : value
}

function submitFlag() {
  if (flagged.value) return
  emit('flag', flagReason.value)
  flagPanelOpen.value = false
  flagReason.value = ''
}

/** 已反馈 (提交成功后由 QuizView 回调置位, 同步显示). */
function markFlagged() {
  flagged.value = true
}

defineExpose({ markFlagged })
</script>

<template>
  <div class="q-card">
    <p class="q-type-tag">
      {{ { choice: '选择题', judge: '判断题', fill: '填空题' }[question.question_type] }}
    </p>
    <h2 class="q-content">{{ question.content }}</h2>

    <!-- 选择: 选项按钮 -->
    <div v-if="question.question_type === 'choice'" class="q-options">
      <button
        v-for="(opt, i) in question.options"
        :key="i"
        class="q-option"
        :class="optionClass(i)"
        :disabled="submitted"
        @click="selected = i"
      >
        <span class="opt-key" aria-hidden="true">{{ 'ABCD'[i] }}</span>
        <span class="opt-text">{{ opt }}</span>
      </button>
    </div>

    <!-- 判断: 对 / 错大按钮 -->
    <div v-else-if="question.question_type === 'judge'" class="q-judge">
      <button
        class="q-judge-btn is-true"
        :class="{ 'is-selected': judgeChoice === 'true' }"
        :disabled="submitted"
        @click="judgeChoice = 'true'"
      >
        <span class="judge-emoji" aria-hidden="true">✓</span>对
      </button>
      <button
        class="q-judge-btn is-false"
        :class="{ 'is-selected': judgeChoice === 'false' }"
        :disabled="submitted"
        @click="judgeChoice = 'false'"
      >
        <span class="judge-emoji" aria-hidden="true">✗</span>错
      </button>
    </div>

    <!-- 填空: 输入框 -->
    <div v-else class="q-fill">
      <el-input
        v-model="fillText"
        class="q-fill-input"
        size="large"
        placeholder="输入你的答案…"
        :disabled="submitted"
        @keyup.enter="submit"
      />
    </div>

    <el-button
      v-if="!submitted"
      type="primary"
      size="large"
      round
      class="q-submit"
      :disabled="!canSubmit"
      @click="submit"
    >
      提交答案
    </el-button>

    <!-- 题目反馈 (Issue 14): 低调入口, 答前答后均可反馈 (讲解有误也是反馈点) -->
    <div class="q-flag">
      <button
        class="q-flag-toggle"
        :disabled="flagged"
        @click="toggleFlagPanel"
      >
        {{ flagged ? '已反馈, 感谢你的帮助 ✓' : '题目有问题? 反馈一下' }}
      </button>

      <div v-if="flagPanelOpen" class="q-flag-panel">
        <p class="q-flag-hint">选择原因 (可选, 直接提交也可):</p>
        <div class="q-flag-reasons">
          <button
            v-for="r in FLAG_REASONS"
            :key="r.value"
            class="q-flag-chip"
            :class="{ 'is-selected': flagReason === r.value }"
            @click="selectFlagReason(r.value)"
          >
            {{ r.label }}
          </button>
        </div>
        <el-button type="primary" size="small" round @click="submitFlag">
          提交反馈
        </el-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.q-card {
  background: var(--cp-card);
  border-radius: var(--cp-radius);
  box-shadow: var(--cp-shadow);
  padding: 28px 28px 24px;
}

.q-type-tag {
  display: inline-block;
  font-size: 12px;
  font-weight: 700;
  color: var(--cp-primary);
  background: var(--cp-primary-soft);
  border-radius: 999px;
  padding: 3px 12px;
  margin: 0 0 12px;
}

.q-content {
  font-size: 19px;
  font-weight: 700;
  line-height: 1.6;
  color: var(--cp-ink);
  margin: 0 0 22px;
}

/* ---- 选择 ---- */
.q-options {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.q-option {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  text-align: left;
  font-size: 15px;
  font-family: inherit;
  color: var(--cp-ink);
  background: var(--cp-primary-soft);
  border: 2px solid transparent;
  border-radius: var(--cp-radius-sm);
  padding: 13px 16px;
  cursor: pointer;
  transition:
    transform 0.15s ease,
    border-color 0.15s ease,
    background 0.15s ease;
}

.q-option:hover:not(:disabled) {
  transform: translateY(-2px);
  border-color: rgba(251, 114, 153, 0.45);
}

.q-option.is-selected {
  border-color: var(--cp-primary);
  background: #fff;
  box-shadow: 0 4px 14px rgba(251, 114, 153, 0.18);
}

/* 答错的所选选项: 琥珀描边提示, 不渲染红色错误氛围 */
.q-option.is-wrong {
  border-color: var(--cp-warn);
  background: #fffaf0;
}

.q-option:disabled {
  cursor: default;
}

.opt-key {
  flex: none;
  width: 26px;
  height: 26px;
  display: grid;
  place-items: center;
  font-size: 12px;
  font-weight: 800;
  border-radius: 50%;
  background: var(--cp-card);
  color: var(--cp-primary);
}

/* ---- 判断 ---- */
.q-judge {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}

.q-judge-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  font-size: 16px;
  font-weight: 700;
  font-family: inherit;
  color: var(--cp-ink);
  background: var(--cp-primary-soft);
  border: 2px solid transparent;
  border-radius: var(--cp-radius);
  padding: 20px 0;
  cursor: pointer;
  transition:
    transform 0.15s ease,
    border-color 0.15s ease;
}

.q-judge-btn:hover:not(:disabled) {
  transform: translateY(-2px);
  border-color: rgba(251, 114, 153, 0.45);
}

.q-judge-btn.is-selected {
  border-color: var(--cp-primary);
  background: #fff;
  box-shadow: 0 4px 14px rgba(251, 114, 153, 0.18);
}

.q-judge-btn:disabled {
  cursor: default;
}

.judge-emoji {
  font-size: 18px;
  font-weight: 900;
}

.is-true .judge-emoji {
  color: var(--cp-ok);
}

.is-false .judge-emoji {
  color: var(--cp-warn);
}

/* ---- 填空 ---- */
.q-fill {
  margin-bottom: 4px;
}

.q-fill-input :deep(.el-input__wrapper) {
  border-radius: var(--cp-radius-sm);
  box-shadow: 0 0 0 1.5px rgba(251, 114, 153, 0.3) inset;
}

.q-submit {
  margin-top: 20px;
  width: 100%;
}

/* ---- 题目反馈 (Issue 14): 低调入口, 不打扰答题氛围 ---- */
.q-flag {
  margin-top: 14px;
  text-align: center;
}

.q-flag-toggle {
  font-size: 12px;
  font-family: inherit;
  color: var(--cp-ink-soft);
  background: none;
  border: none;
  padding: 4px 10px;
  cursor: pointer;
  border-radius: 999px;
  transition: color 0.15s ease, background 0.15s ease;
}

.q-flag-toggle:hover:not(:disabled) {
  color: var(--cp-primary);
  background: var(--cp-primary-soft);
}

.q-flag-toggle:disabled {
  cursor: default;
  opacity: 0.9;
}

.q-flag-panel {
  margin-top: 10px;
  background: var(--cp-primary-soft);
  border-radius: var(--cp-radius-sm);
  padding: 12px 14px;
  text-align: left;
}

.q-flag-hint {
  font-size: 12px;
  color: var(--cp-ink-soft);
  margin: 0 0 8px;
}

.q-flag-reasons {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 10px;
}

.q-flag-chip {
  font-size: 12px;
  font-family: inherit;
  color: var(--cp-ink);
  background: var(--cp-card);
  border: 1.5px solid transparent;
  border-radius: 999px;
  padding: 4px 12px;
  cursor: pointer;
  transition:
    border-color 0.15s ease,
    background 0.15s ease;
}

.q-flag-chip:hover {
  border-color: rgba(251, 114, 153, 0.45);
}

.q-flag-chip.is-selected {
  border-color: var(--cp-primary);
  color: var(--cp-primary);
  font-weight: 600;
}
</style>
