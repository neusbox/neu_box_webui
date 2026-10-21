<script setup>
/**
 * 登录 / 注册：密码登录、邮箱验证码登录、注册。
 * 注册必须填写邮箱；旧用户可在设置页补充邮箱后使用验证码登录。
 */
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import Icon from '../components/Icon.vue'
import { api } from '../api'
import {
  login, loginWithEmailCode, register, requestEmailCode, toast,
} from '../store'

const router = useRouter()
const route = useRoute()

const mode = ref('login')        // 'login' | 'email' | 'register'
const regAllowed = ref(true)
const error = ref('')
const busy = ref(false)
const codeBusy = ref(false)
const countdown = ref(0)
let countdownTimer = null

const username = ref('')
const password = ref('')
const confirmPw = ref('')
const email = ref('')
const code = ref('')

onMounted(async () => {
  try {
    const data = await api.get('/auth/register')
    regAllowed.value = !!data.allowed
  } catch {
    regAllowed.value = false
  }
})
onBeforeUnmount(() => { if (countdownTimer) clearInterval(countdownTimer) })

function switchMode(m) {
  if (m === mode.value) return
  mode.value = m
  error.value = ''
}

function finishRedirect() {
  const next = typeof route.query.next === 'string' ? route.query.next : '/'
  router.push(next)
}

function validEmail(value) {
  return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(String(value || '').trim())
}

async function sendCode() {
  if (!validEmail(email.value)) {
    error.value = '请输入有效邮箱'
    return
  }
  codeBusy.value = true
  error.value = ''
  try {
    const data = await requestEmailCode(email.value.trim())
    // 服务端不泄露邮箱是否存在，统一提示。
    countdown.value = 60
    if (countdownTimer) clearInterval(countdownTimer)
    countdownTimer = setInterval(() => {
      countdown.value -= 1
      if (countdown.value <= 0 && countdownTimer) {
        clearInterval(countdownTimer)
        countdownTimer = null
      }
    }, 1000)
    error.value = ''
    toast(data.message || '如果邮箱已注册，验证码已发送', 'success')
  } catch (e) {
    error.value = e.message
  } finally {
    codeBusy.value = false
  }
}

async function submit() {
  error.value = ''
  busy.value = true
  try {
    if (mode.value === 'email') {
      if (!validEmail(email.value)) throw new Error('请输入有效邮箱')
      if (!/^\d{6}$/.test(code.value.trim())) throw new Error('请输入 6 位验证码')
      await loginWithEmailCode(email.value.trim(), code.value.trim())
    } else if (mode.value === 'register') {
      const name = username.value.trim()
      if (!name || !email.value.trim() || !password.value) {
        throw new Error('请填写用户名、邮箱和密码')
      }
      if (!validEmail(email.value)) throw new Error('请输入有效邮箱')
      if (password.value.length < 4) throw new Error('密码至少 4 位')
      if (password.value !== confirmPw.value) throw new Error('两次输入的密码不一致')
      await register(name, password.value, email.value.trim())
    } else {
      const name = username.value.trim()
      if (!name || !password.value) throw new Error('请输入用户名和密码')
      await login(name, password.value)
    }
    finishRedirect()
  } catch (e) {
    error.value = e.message || '操作失败'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="login-wrap">
    <div class="login-card">
      <div class="login-brand">
        <div class="brand-mark">NO</div>
        <div class="login-title">NeuOps</div>
        <div class="login-sub">AI 运维工单 · 集群 Master</div>
      </div>

      <div class="login-tabs">
        <button class="login-tab" :class="{ active: mode === 'login' }"
                type="button" @click="switchMode('login')">密码登录</button>
        <button class="login-tab" :class="{ active: mode === 'email' }"
                type="button" @click="switchMode('email')">邮箱验证码</button>
        <button v-if="regAllowed" class="login-tab"
                :class="{ active: mode === 'register' }"
                type="button" @click="switchMode('register')">注册</button>
      </div>

      <div v-if="error" class="login-error">{{ error }}</div>

      <form @submit.prevent="submit">
        <div v-if="mode !== 'email'" class="field">
          <label class="field-label">用户名</label>
          <input v-model="username" class="input"
                 :placeholder="mode === 'register' ? '2-32 位字母、数字、_、. 或 -' : '输入用户名'"
                 autocomplete="username" autofocus>
        </div>

        <div v-if="mode !== 'login'" class="field">
          <label class="field-label">邮箱</label>
          <input v-model="email" type="email" class="input"
                 placeholder="用于登录验证码和管理员审批通知"
                 autocomplete="email">
        </div>

        <div v-if="mode !== 'email'" class="field">
          <label class="field-label">密码
            <span v-if="mode === 'register'" class="field-hint">至少 4 位</span>
          </label>
          <input v-model="password" type="password" class="input" placeholder="输入密码"
                 :autocomplete="mode === 'register' ? 'new-password' : 'current-password'">
        </div>

        <div v-if="mode === 'register'" class="field">
          <label class="field-label">确认密码</label>
          <input v-model="confirmPw" type="password" class="input" placeholder="再次输入密码"
                 autocomplete="new-password">
        </div>

        <div v-if="mode === 'email'" class="field">
          <label class="field-label">验证码</label>
          <div class="row" style="gap:8px">
            <input v-model="code" class="input mono" maxlength="6"
                   placeholder="6 位验证码" autocomplete="one-time-code">
            <button class="btn" type="button" style="white-space:nowrap"
                    :disabled="codeBusy || countdown > 0" @click="sendCode">
              <Icon v-if="codeBusy" name="refresh" :size="13" class="spin" />
              {{ countdown > 0 ? `${countdown}s` : '发送验证码' }}
            </button>
          </div>
        </div>

        <button class="btn btn-primary" style="width:100%;padding:9px"
                :disabled="busy" type="submit">
          <Icon v-if="busy" name="refresh" :size="14" class="spin" />
          {{ busy ? '处理中…' : (mode === 'register' ? '注 册' : '登 录') }}
        </button>
      </form>

      <p v-if="mode === 'register'" class="login-footnote">
        注册账号默认为普通用户，邮箱用于验证码登录和管理员审批通知；
        管理员权限由管理员在「用户管理」中授予。
      </p>
      <p v-if="mode === 'email'" class="login-footnote">
        旧用户请先使用密码登录，在「设置」中补充邮箱后即可使用验证码登录。
      </p>
    </div>
  </div>
</template>
