<script setup>
/**
 * 运维工单工作台：新建工单（含 AI 命令建议）、审批列表。
 * root 执行只发生在详情页的管理员审批之后。
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import Icon from '../components/Icon.vue'
import Modal from '../components/Modal.vue'
import { api } from '../api'
import { authRO, toast } from '../store'
import { formatTimeShort, setPolling } from '../utils'

const router = useRouter()
const user = computed(() => authRO.user)
const isAdmin = computed(() => user.value?.role === 'admin')

const nodes = ref([])
const tickets = ref([])
const stats = ref({ by_status: {}, knowledge: 0, ai_configured: false })
const loading = ref(false)
const status = ref('')
const kind = ref('')
const search = ref('')

const statusMeta = {
  draft: { label: '草稿', cls: 'neutral' },
  pending: { label: '待审批', cls: 'queued' },
  approved: { label: '已批准', cls: 'accent' },
  executing: { label: '执行中', cls: 'running' },
  succeeded: { label: '已成功', cls: 'completed' },
  failed: { label: '执行失败', cls: 'failed' },
  rejected: { label: '已驳回', cls: 'failed' },
  cancelled: { label: '已取消', cls: 'offline' },
}
const kindMeta = {
  command: '命令工单',
  consult: '咨询工单',
  knowledge: '知识候选',
}
const riskMeta = {
  unknown: '未知', low: '低风险', medium: '中风险',
  high: '高风险', critical: '严重风险',
}

function statusLabel(value) {
  return statusMeta[value]?.label || value || '—'
}
function statusClass(value) {
  return statusMeta[value]?.cls || 'neutral'
}
function kindLabel(value) {
  return kindMeta[value] || value
}
function riskLabel(value) {
  return riskMeta[value] || value || '—'
}

async function loadNodes() {
  try {
    const data = await api.post('/nodes/get_all_nodes', {})
    nodes.value = data.nodes || []
  } catch { /* 保留旧值 */ }
}

async function loadTickets() {
  loading.value = true
  try {
    const params = new URLSearchParams()
    if (status.value) params.set('status', status.value)
    if (kind.value) params.set('kind', kind.value)
    if (search.value.trim()) params.set('q', search.value.trim())
    const data = await api.get(`/ops/tickets?${params}`)
    tickets.value = data.tickets || []
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    loading.value = false
  }
}

async function loadStats() {
  try { stats.value = await api.get('/ops/stats') } catch { /* 忽略 */ }
}

function refresh() {
  loadNodes()
  loadTickets()
  loadStats()
}

let timer = null
onMounted(() => {
  refresh()
  timer = setPolling(() => { loadTickets(); loadStats() }, 10000)
})
onBeforeUnmount(() => { if (timer) timer() })

function openTicket(ticket) {
  router.push({ name: 'ops-ticket', params: { id: ticket.id } })
}

// ── 新建工单 ────────────────────────────────────────────────
const createOpen = ref(false)
const createBusy = ref(false)
const createForm = reactive({
  kind: 'command',
  nodeId: '',
  title: '',
  request: '',
  command: '',
  workdir: '/root',
  timeout: '3600',
  envText: '',
})
const aiSuggesting = ref(false)
const aiHint = ref(null)
const aiLogs = ref([])

function parseEnv(text) {
  const env = {}
  for (const raw of String(text || '').split('\n')) {
    const line = raw.trim()
    if (!line) continue
    const i = line.indexOf('=')
    if (i <= 0) throw new Error(`环境变量格式错误: ${line}`)
    env[line.slice(0, i).trim()] = line.slice(i + 1)
  }
  return env
}

function openCreate() {
  createForm.kind = 'command'
  createForm.nodeId = nodes.value.find(n => n.status === 'online')?.node_id || ''
  createForm.title = ''
  createForm.request = ''
  createForm.command = ''
  createForm.workdir = '/root'
  createForm.timeout = '3600'
  createForm.envText = ''
  aiHint.value = null
  aiLogs.value = []
  createOpen.value = true
}

function pushAiLog(text) {
  if (!text) return
  aiLogs.value.push({ id: `${Date.now()}-${aiLogs.value.length}`, text })
}

// 根据“需求描述 + 目标节点”运行只读工具 Agent；只填充表单，不创建工单。
async function suggestCommand() {
  if (!createForm.nodeId) { toast('请先选择目标节点', 'error'); return }
  if (!createForm.request.trim()) { toast('请先填写需求描述', 'error'); return }
  aiSuggesting.value = true
  aiHint.value = null
  aiLogs.value = []
  try {
    await api.stream('/ops/assistant/agent', {
      node_id: createForm.nodeId,
      request: createForm.request.trim(),
    }, (event) => {
      if (event.type === 'log') {
        pushAiLog(event.message || '')
      } else if (event.type === 'tool_start') {
        pushAiLog(`→ ${event.tool || 'tool'} ${JSON.stringify(event.args || {})}`)
      } else if (event.type === 'tool_result') {
        const exit = event.exit_code == null ? '?' : event.exit_code
        const preview = String(event.output || '').trim().split('\n')[0].slice(0, 160)
        pushAiLog(`✓ ${event.tool || 'tool'} exit=${exit}${preview ? ` · ${preview}` : ''}`)
      } else if (event.type === 'result') {
        const data = event.result || {}
        aiHint.value = data
        const command = String(data.suggestion?.command || '').trim()
        if (command) createForm.command = command
        pushAiLog('AI 已生成最终命令建议')
        toast(command ? '已生成命令建议，请确认后再提交' : 'AI 未生成命令', command ? 'success' : 'error')
      }
    })
  } catch (e) {
    pushAiLog(`AI 失败：${e.message}`)
    toast(e.message, 'error')
  } finally {
    aiSuggesting.value = false
  }
}

async function submitCreate() {
  if (!createForm.nodeId) { toast('请选择目标节点', 'error'); return }
  if (createForm.request.length > 1000) {
    toast('需求描述最多 1000 字', 'error')
    return
  }
  if (!createForm.request.trim() && !createForm.command.trim()) {
    toast('请填写需求或命令', 'error')
    return
  }
  createBusy.value = true
  try {
    const body = {
      kind: createForm.kind,
      node_id: createForm.nodeId,
      target_node_id: createForm.nodeId,
      title: createForm.title.trim() || undefined,
      request: createForm.request.trim(),
      command: createForm.command.trim(),
      exec_env: {
        user: 'root',
        shell: 'bash',
        login: true,
        workdir: createForm.workdir.trim() || '/root',
        timeout_seconds: Number(createForm.timeout) || 3600,
        env: parseEnv(createForm.envText),
      },
      status: 'pending',
      ai_suggestion: aiHint.value?.suggestion || undefined,
      ai_model: aiHint.value?.suggestion?.model || undefined,
    }
    const data = await api.post('/ops/tickets', body)
    createOpen.value = false
    toast('工单已提交，等待管理员审批', 'success')
    await loadTickets()
    await loadStats()
    if (data.ticket?.id) openTicket(data.ticket)
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    createBusy.value = false
  }
}

</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h1 class="page-title">运维工单</h1>
        <div class="page-desc">AI 命令建议 → 管理员审批 → 固定 root 身份单次执行。普通用户与 AI 均不能直接执行特权命令。</div>
      </div>
      <div class="page-actions">
        <button class="btn btn-primary" @click="openCreate">
          <Icon name="plus" :size="14" /> 新建工单
        </button>
      </div>
    </div>

    <div class="stat-grid" style="margin-bottom:16px">
      <div class="stat card">
        <div class="stat-label"><Icon name="clock" :size="13" /> 待审批</div>
        <div class="stat-value">{{ stats.by_status?.pending || 0 }}</div>
      </div>
      <div class="stat card">
        <div class="stat-label"><Icon name="check" :size="13" /> 已批准</div>
        <div class="stat-value">{{ stats.by_status?.approved || 0 }}</div>
      </div>
      <div class="stat card">
        <div class="stat-label"><Icon name="play" :size="13" /> 执行中</div>
        <div class="stat-value">{{ stats.by_status?.executing || 0 }}</div>
      </div>
      <div class="stat card">
        <div class="stat-label"><Icon name="folder" :size="13" /> 知识库</div>
        <div class="stat-value">{{ stats.knowledge || 0 }}</div>
      </div>
    </div>

    <div class="card">
      <div class="card-head">
        <div class="row grow" style="gap:8px;flex-wrap:wrap">
          <div class="row" style="gap:6px">
            <Icon name="search" :size="14" class="text-3" />
            <input v-model="search" class="input" style="width:220px;padding:5px 10px;font-size:12.5px"
                   placeholder="搜索标题 / 需求 / 命令…" @keyup.enter="loadTickets">
          </div>
          <select v-model="status" class="select" style="width:130px;padding:5px 9px;font-size:12.5px"
                  @change="loadTickets">
            <option value="">全部状态</option>
            <option value="draft">草稿</option>
            <option value="pending">待审批</option>
            <option value="approved">已批准</option>
            <option value="executing">执行中</option>
            <option value="succeeded">已成功</option>
            <option value="failed">执行失败</option>
            <option value="rejected">已驳回</option>
            <option value="cancelled">已取消</option>
          </select>
          <select v-model="kind" class="select" style="width:130px;padding:5px 9px;font-size:12.5px"
                  @change="loadTickets">
            <option value="">全部类型</option>
            <option value="command">命令工单</option>
            <option value="consult">咨询工单</option>
            <option value="knowledge">知识候选</option>
          </select>
          <button class="btn btn-sm" @click="loadTickets">搜索</button>
        </div>
        <span class="card-sub">{{ isAdmin ? '管理员视图：全部工单' : '仅显示我的工单' }}</span>
        <button class="btn btn-ghost btn-icon" title="刷新" @click="refresh">
          <Icon name="refresh" :size="14" :class="{ spin: loading }" />
        </button>
      </div>

      <div class="card-body flush">
        <div v-if="!tickets.length" class="empty-state">
          <div class="icon">🛡️</div>
          <p>暂无工单。点击右上角「新建工单」开始；可在表单内使用 AI 命令建议。</p>
        </div>
        <div v-else class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th>工单</th>
                <th>类型</th>
                <th>状态</th>
                <th>节点</th>
                <th>风险</th>
                <th>提交人</th>
                <th>更新时间</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="t in tickets" :key="t.id" style="cursor:pointer" @click="openTicket(t)">
                <td>
                  <div class="cell-main">{{ t.title || t.request || t.id }}</div>
                  <div class="cell-sub mono">{{ t.id }}</div>
                </td>
                <td>{{ kindLabel(t.kind) }}</td>
                <td><span class="badge" :class="statusClass(t.status)"><span class="dot" />{{ statusLabel(t.status) }}</span></td>
                <td>{{ t.target_node_name || '—' }}</td>
                <td>{{ riskLabel(t.risk_level) }}</td>
                <td>{{ t.created_by || '—' }}</td>
                <td class="cell-sub">{{ formatTimeShort(t.updated_at || t.created_at) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- 新建工单 -->
    <Modal v-model="createOpen" title="新建运维工单" wide>
      <div class="input-row">
        <div class="field">
          <label class="field-label">类型</label>
          <select v-model="createForm.kind" class="select">
            <option value="command">命令工单（填写需求或命令）</option>
            <option value="consult">咨询工单（请管理员协助制定方案）</option>
          </select>
        </div>
        <div class="field">
          <label class="field-label">目标节点</label>
          <select v-model="createForm.nodeId" class="select" @change="aiHint = null">
            <option value="">请选择</option>
            <option v-for="n in nodes" :key="n.node_id" :value="n.node_id">
              {{ n.name }}（{{ n.status }}）
            </option>
          </select>
        </div>
      </div>
      <div class="field">
        <label class="field-label">标题</label>
        <input v-model="createForm.title" class="input" placeholder="可选；默认取需求/命令摘要">
      </div>
      <div class="field">
        <label class="field-label">需求描述</label>
        <textarea v-model="createForm.request" class="textarea" maxlength="1000"
                  placeholder="例如：/var 分区快满了，需要定位大文件并安全清理"
                  @input="aiHint = null"></textarea>
        <div class="field-hint" style="text-align:right">{{ createForm.request.length }}/1000</div>
      </div>
      <div class="field">
        <div class="row" style="gap:8px;justify-content:space-between">
          <label class="field-label">建议命令（可选）</label>
          <button class="btn btn-sm" type="button"
                  :disabled="aiSuggesting || !createForm.request.trim() || !createForm.nodeId"
                  @click="suggestCommand">
            <Icon :name="aiSuggesting ? 'refresh' : 'star'" :size="13"
                  :class="{ spin: aiSuggesting }" />
            {{ aiSuggesting ? '生成中…' : 'AI命令建议' }}
          </button>
        </div>
        <textarea v-model="createForm.command" class="textarea mono" rows="5"
                  placeholder="完整 Shell 命令；管理员批准前不会执行"></textarea>
        <div class="field-hint">命令将以 root 身份在目标节点执行；提交后仍需管理员审批。</div>
      </div>

      <div v-if="aiLogs.length" style="margin-bottom:14px;max-height:170px;overflow:auto;background:var(--bg);border:1px solid var(--border);border-radius:var(--radius-sm);padding:8px 10px;font-family:var(--mono);font-size:11.5px;line-height:1.6">
        <div v-for="item in aiLogs" :key="item.id" style="white-space:pre-wrap;word-break:break-word">{{ item.text }}</div>
      </div>

      <div v-if="aiHint" class="card" style="margin-bottom:14px;background:var(--surface-2)">
        <div class="card-head">
          <span class="card-title">AI 命令建议</span>
          <span class="spacer" />
          <span class="badge" :class="aiHint.suggestion?.risk_level === 'critical' ? 'failed' : 'accent'">
            {{ riskLabel(aiHint.suggestion?.risk_level) }}
          </span>
        </div>
        <div class="card-body" style="padding:12px 16px">
          <div v-if="aiHint.warning" class="login-error" style="margin-bottom:8px">{{ aiHint.warning }}</div>
          <p v-if="aiHint.suggestion?.reply" style="white-space:pre-wrap;margin:0 0 8px">{{ aiHint.suggestion.reply }}</p>
          <p v-if="aiHint.suggestion?.explanation" style="white-space:pre-wrap;margin:0 0 8px;color:var(--text-2)">{{ aiHint.suggestion.explanation }}</p>
          <ul v-if="aiHint.suggestion?.risk_notes?.length" style="margin:0;padding-left:20px;color:var(--text-2)">
            <li v-for="note in aiHint.suggestion.risk_notes" :key="note">{{ note }}</li>
          </ul>
        </div>
      </div>
      <div class="input-row">
        <div class="field">
          <label class="field-label">工作目录</label>
          <input v-model="createForm.workdir" class="input mono" placeholder="/root">
        </div>
        <div class="field">
          <label class="field-label">超时（秒）</label>
          <input v-model="createForm.timeout" class="input mono" placeholder="3600">
        </div>
      </div>
      <div class="field">
        <label class="field-label">环境变量（每行 KEY=value，可选）</label>
        <textarea v-model="createForm.envText" class="textarea mono" rows="3" />
      </div>
      <template #footer>
        <button class="btn" @click="createOpen = false">取消</button>
        <button class="btn btn-primary" :disabled="createBusy" @click="submitCreate">
          <Icon v-if="createBusy" name="refresh" :size="14" class="spin" />
          提交审批
        </button>
      </template>
    </Modal>

  </div>
</template>
