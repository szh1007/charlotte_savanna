<script setup lang="ts">
// 注册页 (Issue 02): 注册成功后跳登录页提示 (注册不自动登录, 对齐 minimall).
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { ApiError } from '@/api/client'
import { useAuth } from '@/stores/auth'

const router = useRouter()
const { register } = useAuth()

const formRef = ref<FormInstance>()
const loading = ref(false)
const form = reactive({
  username: '',
  email: '',
  password: '',
  confirm: '',
})

const rules: FormRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 2, max: 150, message: '用户名长度 2-150 个字符', trigger: 'blur' },
  ],
  email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { type: 'email', message: '邮箱格式不正确', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 8, message: '密码至少 8 位', trigger: 'blur' },
  ],
  confirm: [
    { required: true, message: '请再次输入密码', trigger: 'blur' },
    {
      validator: (_rule, value: string, callback) => {
        if (value !== form.password) callback(new Error('两次输入的密码不一致'))
        else callback()
      },
      trigger: 'blur',
    },
  ],
}

async function onSubmit() {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  loading.value = true
  try {
    await register({
      username: form.username,
      email: form.email,
      password: form.password,
    })
    ElMessage.success('注册成功，请登录')
    router.push({ name: 'login', query: { registered: '1' } })
  } catch (e) {
    if (e instanceof ApiError) {
      // 字段级错误映射到表单项 (DRF 返回 {username: [...], ...})
      const payload = e.payload as Record<string, unknown> | null
      if (payload && typeof payload === 'object' && formRef.value) {
        let mapped = false
        for (const key of Object.keys(form)) {
          const errors = payload[key]
          if (Array.isArray(errors) && typeof errors[0] === 'string') {
            formRef.value.fields.forEach((f) => {
              if (f.prop === key) f.validateMessage = errors[0]
            })
            mapped = true
          }
        }
        if (mapped) return
      }
      ElMessage.error(e.message)
    } else {
      ElMessage.error('注册失败，请稍后重试')
    }
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="auth-page">
    <div class="auth-card">
      <div class="auth-brand">
        <p class="auth-eyebrow">JOIN CHARPLOT</p>
        <h1 class="auth-title">创建你的闯关账号</h1>
        <p class="auth-sub">一句想学的话，就能开启第一关</p>
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
          <el-input v-model="form.username" placeholder="怎么称呼你" autocomplete="username" />
        </el-form-item>
        <el-form-item label="邮箱" prop="email">
          <el-input v-model="form.email" placeholder="you@example.com" autocomplete="email" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="至少 8 位"
            show-password
            autocomplete="new-password"
          />
        </el-form-item>
        <el-form-item label="确认密码" prop="confirm">
          <el-input
            v-model="form.confirm"
            type="password"
            placeholder="再输一遍"
            show-password
            autocomplete="new-password"
          />
        </el-form-item>
        <el-button class="auth-submit" type="primary" round :loading="loading" @click="onSubmit">
          注册
        </el-button>
      </el-form>

      <p class="auth-switch">
        已有账号？
        <router-link to="/login" class="auth-link">直接登录</router-link>
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

.auth-eyebrow {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 2px;
  color: var(--cp-primary);
  margin: 0 0 10px;
}

.auth-title {
  margin: 0 0 6px;
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
