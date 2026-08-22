<script setup lang="ts">
// 登录页 (Issue 02): 居中白卡 + 渐变主按钮, 成功后跳转 redirect.
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { ApiError } from '@/api/client'
import { useAuth } from '@/stores/auth'

const router = useRouter()
const route = useRoute()
const { state, login } = useAuth()

const formRef = ref<FormInstance>()
const loading = ref(false)
const form = reactive({ username: '', password: '' })

const rules: FormRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function onSubmit() {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  loading.value = true
  try {
    await login(form.username, form.password)
    ElMessage.success(`欢迎回来，${state.user?.username ?? ''}`)
    const redirect =
      typeof route.query.redirect === 'string' ? route.query.redirect : '/'
    router.push(redirect)
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '登录失败，请稍后重试')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="auth-page">
    <div class="auth-card">
      <div class="auth-brand">
        <div class="brand-logo" aria-hidden="true">
          <svg viewBox="0 0 64 64" width="44" height="44">
            <defs>
              <linearGradient id="logoGradLogin" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0" stop-color="#fb7299" />
                <stop offset="1" stop-color="#c9b6e4" />
              </linearGradient>
            </defs>
            <rect x="2" y="2" width="60" height="60" rx="16" fill="url(#logoGradLogin)" />
            <path
              d="M18 46V18l28 28V18"
              stroke="#fff"
              stroke-width="6"
              stroke-linecap="round"
              stroke-linejoin="round"
              fill="none"
            />
          </svg>
        </div>
        <h1 class="auth-title">回到闯关地图</h1>
        <p class="auth-sub">登录后继续你的学习旅程，连胜不等待</p>
      </div>

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        size="large"
        @submit.prevent="onSubmit"
      >
        <el-form-item label="用户名" prop="username">
          <el-input v-model="form.username" placeholder="输入用户名" autocomplete="username" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="输入密码"
            show-password
            autocomplete="current-password"
          />
        </el-form-item>
        <el-button class="auth-submit" type="primary" round :loading="loading" @click="onSubmit">
          登录
        </el-button>
      </el-form>

      <p class="auth-switch">
        还没有账号？
        <router-link to="/register" class="auth-link">立即注册</router-link>
      </p>
    </div>
  </div>
</template>

<style scoped>
.auth-page {
  min-height: calc(100vh - 200px);
  display: grid;
  place-items: center;
  padding: 24px;
}

.auth-card {
  width: 100%;
  max-width: 400px;
  background: var(--cp-card);
  border-radius: var(--cp-radius);
  box-shadow: var(--cp-shadow);
  padding: 40px 36px 32px;
}

.auth-brand {
  text-align: center;
  margin-bottom: 28px;
}

.brand-logo {
  display: inline-flex;
  filter: drop-shadow(0 6px 14px rgba(251, 114, 153, 0.35));
}

.auth-title {
  margin: 16px 0 6px;
  font-size: 24px;
  font-weight: 800;
  letter-spacing: -0.5px;
  color: var(--cp-ink);
  background: linear-gradient(120deg, var(--cp-primary), var(--cp-accent-lilac));
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}

.auth-sub {
  margin: 0;
  font-size: 13px;
  color: var(--cp-ink-soft);
}

.auth-submit {
  width: 100%;
  margin-top: 8px;
  height: 44px;
  font-size: 15px;
  font-weight: 700;
  background: linear-gradient(135deg, var(--cp-primary), var(--cp-accent-lilac));
  border: none;
}

.auth-switch {
  margin: 24px 0 0;
  text-align: center;
  font-size: 13px;
  color: var(--cp-ink-soft);
}

.auth-link {
  color: var(--cp-primary);
  font-weight: 700;
  text-decoration: none;
}

.auth-link:hover {
  text-decoration: underline;
}
</style>
