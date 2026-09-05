<script setup>
/**
 * 设置：用户信息 + 节点凭据（节点上的 OS 用户名/密码）+ 修改密码。
 *
 * 节点凭据语义：
 *   - OS 用户名：提交任务到该节点时作为 worker 侧 user_id 归属显示
 *     （未设置时默认用 WebUI 用户名）
 *   - 密码：Fernet 加密存于 master，仅本人可见（供登录该节点参考）
 */
import { computed, onMounted, reactive, ref } from 'vue'
import Icon from '../components/Icon.vue'
import { api } from '../api'
import { auth, toast } from '../store'
import { formatTime } from '../utils'

const user = computed(() => auth.user)

// ── 修改密码 ────────────────────────────────────────────────
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

// ── 节点凭据 ────────────────────────────────────────────────
const nodes = ref([])          // [{node_id, name, status, ...}]
const creds = reactive({})     // node_name → {username, has_password, ...}
const rows = reactive({})      // node_name → {username, password, show, saving}
const credLoading = ref(false)

async function loadCreds() {
  credLoading.value = true
  try {
    const [nodeData, credData] = await Promise.all([
      api.post('/nodes/get_all_nodes', {}),
      api.get('/auth/credentials'),
    ])
    nodes.value = nodeData.nodes || []
    for (const k of Object.keys(creds)) delete creds[k]
    for (const c of credData.credentials || []) {
      creds[c.node_name] = c
      if (!rows[c.node_name]) {
        rows[c.node_name] = { username: c.username, password: '', show: false, saving: false }
      }
    }
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    credLoading.value = false
  }
}

function rowFor(nodeName) {
  if (!rows[nodeName]) {
    const c = creds[nodeName]
    rows[nodeName] = c
      ? { username: c.username, password: '', show: false, saving: false }
      : { username: '', password: '', show: false, saving: false }
  }
  return rows[nodeName]
}

async function saveCred(nodeName) {
  const row = rowFor(nodeName)
  const username = row.username.trim()
  if (!username) {
    toast('请填写节点上的用户名', 'error')
    return
  }
  const body = { username }
  if (row.password) body.password = row.password   // 空 = 保持已有密码
  row.saving = true
  try {
    await api.put(`/auth/credentials/${encodeURIComponent(nodeName)}`, body)
    toast(`节点 "${nodeName}" 凭据已保存`, 'success')
    row.password = ''
    row.show = false
    await loadCreds()
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    row.saving = false
  }
}

async function clearPw(nodeName) {
  const row = rowFor(nodeName)
  if (!window.confirm(`确定要清除节点 "${nodeName}" 保存的密码吗？`)) return
  row.saving = true
  try {
    await api.put(`/auth/credentials/${encodeURIComponent(nodeName)}`,
                  { username: row.username.trim() || creds[nodeName]?.username, password: '' })
    toast(`节点 "${nodeName}" 密码已清除`, 'success')
    row.password = ''
    row.show = false
    await loadCreds()
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    row.saving = false
  }
}

async function revealPw(nodeName) {
  const row = rowFor(nodeName)
  try {
    const data = await api.get(`/auth/credentials/${encodeURIComponent(nodeName)}/password`)
    if (data.password) {
      row.password = data.password
      row.show = true
    } else {
      toast('该节点未保存密码', 'info')
    }
  } catch (e) {
    toast(e.message, 'error')
  }
}

async function removeCred(nodeName) {
  if (!window.confirm(`确定要删除节点 "${nodeName}" 的凭据吗？`)) return
  try {
    await api.delete(`/auth/credentials/${encodeURIComponent(nodeName)}`)
    toast(`节点 "${nodeName}" 凭据已删除`, 'success')
    delete creds[nodeName]
    delete rows[nodeName]
  } catch (e) {
    toast(e.message, 'error')
  }
}

onMounted(loadCreds)
</script>

<template>
  <div style="max-width:760px">
    <div class="page-head">
      <div>
        <h1 class="page-title">设置</h1>
        <div class="page-desc">账户信息、节点凭据与密码管理</div>
      </div>
    </div>

    <!-- 用户信息 -->
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
          提示：WebUI 用户名用于平台内身份（注册/登录、实验归属）。
          提交任务到节点时，若下方为该节点设置了 OS 用户名，则任务在
          节点侧以该用户名归属，否则以 WebUI 用户名归属。
        </p>
      </div>
    </div>

    <!-- 节点凭据 -->
    <div class="card">
      <div class="card-head">
        <Icon name="key" :size="14" />
        <span class="card-title">节点凭据</span>
        <span class="card-sub">节点上的 OS 用户名 + 密码（仅本人可见）</span>
        <span class="grow" />
        <button class="btn btn-ghost btn-sm" @click="loadCreds">
          <Icon name="refresh" :size="13" :class="{ spin: credLoading }" /> 刷新
        </button>
      </div>
      <div class="card-body flush">
        <div class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th style="width:130px">节点</th>
                <th>节点上的用户名</th>
                <th style="width:230px">密码</th>
                <th style="text-align:right">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!nodes.length">
                <td colspan="4" class="queue-empty">
                  尚未发现节点。节点由管理员在「节点管理」中配置。
                </td>
              </tr>
              <tr v-for="n in nodes" :key="n.node_id">
                <td>
                  <div class="cell-main">{{ n.name }}</div>
                  <div class="text-3 small mono">{{ n.ip }}:{{ n.port }}</div>
                </td>
                <td>
                  <input :value="rowFor(n.name).username"
                         @input="rowFor(n.name).username = $event.target.value"
                         class="input input-sm mono"
                         placeholder="如 al（默认同 WebUI 用户名）"
                         :disabled="rowFor(n.name).saving">
                </td>
                <td>
                  <div class="row" style="gap:6px">
                    <input :value="rowFor(n.name).password"
                           @input="rowFor(n.name).password = $event.target.value; rowFor(n.name).show = true"
                           :type="rowFor(n.name).show ? 'text' : 'password'"
                           class="input input-sm mono grow"
                           :placeholder="creds[n.name]?.has_password ? '••••••（已保存）' : '未设置'"
                           autocomplete="new-password"
                           :disabled="rowFor(n.name).saving">
                    <button v-if="creds[n.name]?.has_password" class="btn btn-ghost btn-icon"
                            title="查看已保存密码" @click="revealPw(n.name)">
                      <Icon name="eye" :size="14" />
                    </button>
                  </div>
                </td>
                <td>
                  <div class="row" style="justify-content:flex-end;gap:6px">
                    <button class="btn btn-primary btn-sm" :disabled="rowFor(n.name).saving"
                            @click="saveCred(n.name)">
                      {{ rowFor(n.name).saving ? '保存中…' : '保存' }}
                    </button>
                    <button v-if="creds[n.name]" class="btn btn-ghost btn-icon" title="清除密码"
                            :disabled="rowFor(n.name).saving" @click="clearPw(n.name)">
                      <Icon name="close" :size="13" />
                    </button>
                    <button v-if="creds[n.name]" class="btn btn-danger btn-icon" title="删除凭据"
                            :disabled="rowFor(n.name).saving" @click="removeCred(n.name)">
                      <Icon name="trash" :size="13" />
                    </button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="text-3 small" style="padding:10px 16px">
          用户名：提交任务到该节点时作为 worker 侧 user_id（节点队列中的任务归属显示名），
          留空则默认使用 WebUI 用户名。密码：加密保存在 master 侧，仅供你登录该节点时参考，
          不会发送给 worker。
        </p>
      </div>
    </div>

    <!-- 修改密码 -->
    <div class="card">
      <div class="card-head">
        <Icon name="key" :size="14" />
        <span class="card-title">修改密码</span>
        <span class="card-sub">WebUI 登录密码</span>
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
