<script setup>
/**
 * 设置：用户信息 + 修改密码。
 */
import { computed, ref } from 'vue'
import Icon from '../components/Icon.vue'
import { api } from '../api'
import { auth, toast } from '../store'
import { formatTime } from '../utils'

const user = computed(() => auth.user)

const oldPw = ref('')
const newPw = ref('')
const confirmPw = ref('')
const msg = ref(null)          // {type, text}
const busy = ref(false)

async function changePassword() {
  msg.value = null
  if (!oldPw.value || !newPw.value || !confirmPw.value) {
    msg.value = { type: 'error', text: '请填写所有密码字段' }
    return
  }
  if (newPw.value !== confirmPw.value) {
    msg.value = { type: 'error', text: '两次输入的新密码不一致' }
    return
  }
  if (newPw.value.length < 4) {
    msg.value = { type: 'error', text: '新密码至少 4 位' }
    return
  }
  if (oldPw.value === newPw.value) {
    msg.value = { type: 'error', text: '新密码不能与旧密码相同' }
    return
  }
  busy.value = true
  try {
    const data = await api.put('/auth/password', {
      old_password: oldPw.value,
      new_password: newPw.value,
    })
    msg.value = { type: 'success', text: data.message || '密码已修改，请重新登录' }
    oldPw.value = newPw.value = confirmPw.value = ''
  } catch (e) {
    msg.value = { type: 'error', text: e.message }
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div style="max-width:640px">
    <div class="page-head">
      <div>
        <h1 class="page-title">设置</h1>
        <div class="page-desc">账户信息与密码管理</div>
      </div>
    </div>

    <div class="card">
      <div class="card-head">
        <Icon name="users" :size="14" />
        <span class="card-title">用户信息</span>
      </div>
      <div class="card-body">
        <div class="table-wrap">
          <table class="table">
            <tbody>
              <tr>
                <td style="width:120px" class="text-3">用户名</td>
                <td class="cell-main mono">{{ user?.username }}</td>
              </tr>
              <tr>
                <td class="text-3">角色</td>
                <td>
                  <span class="badge" :class="user?.role === 'admin' ? 'accent' : 'neutral'">
                    {{ user?.role === 'admin' ? '管理员' : '普通用户' }}
                  </span>
                </td>
              </tr>
              <tr v-if="user?.created_at">
                <td class="text-3">创建时间</td>
                <td class="mono small">{{ formatTime(user.created_at) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="text-3 small mt-8 mb-8">
          提示：你提交的任务将以该用户名归属（worker 上的 user_id），
          “我的队列”按此匹配。
        </p>
      </div>
    </div>

    <div class="card">
      <div class="card-head">
        <Icon name="key" :size="14" />
        <span class="card-title">修改密码</span>
      </div>
      <div class="card-body">
        <div class="field">
          <label class="field-label">旧密码</label>
          <input v-model="oldPw" type="password" class="input" autocomplete="off">
        </div>
        <div class="field">
          <label class="field-label">新密码 <span class="field-hint">至少 4 位</span></label>
          <input v-model="newPw" type="password" class="input" autocomplete="new-password">
        </div>
        <div class="field">
          <label class="field-label">确认新密码</label>
          <input v-model="confirmPw" type="password" class="input" autocomplete="new-password">
        </div>
        <div v-if="msg" class="small mb-8" :class="msg.type === 'error' ? 'text-danger' : ''"
             :style="msg.type === 'success' ? 'color:var(--success)' : ''">
          {{ msg.text }}
        </div>
        <button class="btn btn-primary" :disabled="busy" @click="changePassword">
          {{ busy ? '修改中…' : '修改密码' }}
        </button>
      </div>
    </div>
  </div>
</template>
