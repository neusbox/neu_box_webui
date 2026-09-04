<script setup>
/**
 * 用户管理（admin）：列表、创建、禁用/启用、改角色、重置密码。
 */
import { onMounted, ref } from 'vue'
import Icon from '../components/Icon.vue'
import Modal from '../components/Modal.vue'
import { api } from '../api'
import { toast } from '../store'
import { formatTime } from '../utils'

const users = ref([])
const loading = ref(false)

const createOpen = ref(false)
const createForm = ref({ username: '', password: '', role: 'user' })

const resetOpen = ref(false)
const resetTarget = ref(null)
const resetPassword = ref('')

async function load() {
  loading.value = true
  try {
    const data = await api.get('/admin/users')
    users.value = data.users || []
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    loading.value = false
  }
}

async function createUser() {
  const f = createForm.value
  if (!f.username.trim() || !f.password) {
    toast('请填写用户名和密码', 'error')
    return
  }
  try {
    await api.post('/admin/users', {
      username: f.username.trim(),
      password: f.password,
      role: f.role,
    })
    toast(`用户 "${f.username.trim()}" 已创建`, 'success')
    createOpen.value = false
    createForm.value = { username: '', password: '', role: 'user' }
    load()
  } catch (e) {
    toast(e.message, 'error')
  }
}

async function toggleActive(u) {
  const active = !u.is_active
  try {
    await api.put(`/admin/users/${u.id}`, { is_active: active })
    toast(active ? `已启用 "${u.username}"` : `已禁用 "${u.username}"`, 'success')
    load()
  } catch (e) {
    toast(e.message, 'error')
  }
}

async function changeRole(u, role) {
  if (role === u.role) return
  try {
    await api.put(`/admin/users/${u.id}`, { role })
    toast(`"${u.username}" 角色已更新`, 'success')
    load()
  } catch (e) {
    toast(e.message, 'error')
    load()
  }
}

function openReset(u) {
  resetTarget.value = u
  resetPassword.value = ''
  resetOpen.value = true
}
async function confirmReset() {
  if (resetPassword.value.length < 4) {
    toast('密码至少 4 位', 'error')
    return
  }
  try {
    await api.post(`/admin/users/${resetTarget.value.id}/password`, {
      password: resetPassword.value,
    })
    toast(`"${resetTarget.value.username}" 密码已重置`, 'success')
    resetOpen.value = false
  } catch (e) {
    toast(e.message, 'error')
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h1 class="page-title">用户管理</h1>
        <div class="page-desc">创建账户、管理角色与启用状态</div>
      </div>
      <div class="page-actions">
        <button class="btn btn-ghost btn-icon" :class="{ spin: loading }"
                title="刷新" @click="load">
          <Icon name="refresh" :size="15" />
        </button>
        <button class="btn btn-primary" @click="createOpen = true">
          <Icon name="plus" :size="14" /> 新建用户
        </button>
      </div>
    </div>

    <div class="card">
      <div class="card-body flush">
        <div class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th>用户名</th>
                <th>角色</th>
                <th>状态</th>
                <th>创建时间</th>
                <th>最近登录</th>
                <th style="text-align:right">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="u in users" :key="u.id">
                <td>
                  <div class="cell-main mono">{{ u.username }}</div>
                </td>
                <td>
                  <select class="select" style="width:96px;padding:4px 8px;font-size:12px"
                          :value="u.role" @change="changeRole(u, $event.target.value)">
                    <option value="user">用户</option>
                    <option value="admin">管理员</option>
                  </select>
                </td>
                <td>
                  <span class="badge" :class="u.is_active ? 'completed' : 'offline'">
                    <span class="dot" />{{ u.is_active ? '启用' : '禁用' }}
                  </span>
                </td>
                <td class="text-3 small mono">{{ formatTime(u.created_at) }}</td>
                <td class="text-3 small mono">{{ u.last_login_at ? formatTime(u.last_login_at) : '—' }}</td>
                <td>
                  <div class="row" style="justify-content:flex-end">
                    <button class="btn btn-ghost btn-sm" @click="openReset(u)">
                      <Icon name="key" :size="13" /> 重置密码
                    </button>
                    <button class="btn btn-sm" :class="u.is_active ? 'btn-danger' : 'btn-primary'"
                            @click="toggleActive(u)">
                      {{ u.is_active ? '禁用' : '启用' }}
                    </button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- 新建用户 -->
    <Modal v-model="createOpen" title="新建用户">
      <div class="field">
        <label class="field-label">用户名 <span class="field-hint">2-32 位字母/数字/_.-</span></label>
        <input v-model="createForm.username" class="input mono"
               placeholder="alice" autocomplete="off">
      </div>
      <div class="field">
        <label class="field-label">初始密码 <span class="field-hint">至少 4 位</span></label>
        <input v-model="createForm.password" type="password" class="input"
               autocomplete="new-password">
      </div>
      <div class="field">
        <label class="field-label">角色</label>
        <select v-model="createForm.role" class="select">
          <option value="user">普通用户</option>
          <option value="admin">管理员</option>
        </select>
      </div>
      <template #footer>
        <button class="btn" @click="createOpen = false">取消</button>
        <button class="btn btn-primary" @click="createUser">创建</button>
      </template>
    </Modal>

    <!-- 重置密码 -->
    <Modal v-model="resetOpen" title="重置密码">
      <p class="text-2" style="margin-top:0">
        为用户 <span class="mono" style="font-weight:600">{{ resetTarget?.username }}</span> 设置新密码：
      </p>
      <div class="field">
        <input v-model="resetPassword" type="password" class="input"
               placeholder="新密码（至少 4 位）" autocomplete="new-password">
      </div>
      <template #footer>
        <button class="btn" @click="resetOpen = false">取消</button>
        <button class="btn btn-primary" @click="confirmReset">重置</button>
      </template>
    </Modal>
  </div>
</template>
