<script setup>
/**
 * 工单详情：查看建议/审批快照/执行结果；管理员在此批准、驳回、执行。
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Icon from '../components/Icon.vue'
import Modal from '../components/Modal.vue'
import { api } from '../api'
import { authRO, toast } from '../store'
import { downloadText, formatTime, formatTimeShort, setPolling } from '../utils'

const route = useRoute()
const router = useRouter()
const user = computed(() => authRO.user)
const isAdmin = computed(() => user.value?.role === 'admin')

const ticket = ref(null)
const loading = ref(false)
const busy = ref('')
const logText = ref('')
const logResult = ref(null)
const logTab = ref('stdout')

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
  command: '命令工单', consult: '咨询工单', knowledge: '知识候选',
}
const riskMeta = {
  unknown: '未知', low: '低风险', medium: '中风险',
  high: '高风险', critical: '严重风险',
}
function statusLabel(v) { return statusMeta[v]?.label || v || '—' }
function statusClass(v) { return statusMeta[v]?.cls || 'neutral' }
function kindLabel(v) { return kindMeta[v] || v }
function riskLabel(v) { return riskMeta[v] || v || '—' }

const approval = reactive({
  command: '',
  reason: '',
  workdir: '/root',
  timeout: '3600',
  envText: '',
})
const knowledgeEdit = reactive({ title: '', content: '', tags: '' })
const rejectOpen = ref(false)
const rejectReason = ref('')

function syncApprovalForm(t) {
  approval.command = t.command || ''
  approval.reason = ''
  const env = t.exec_env || {}
  approval.workdir = env.workdir || '/root'
  approval.timeout = String(env.timeout_seconds || 3600)
  approval.envText = Object.entries(env.env || {})
    .map(([k, v]) => `${k}=${v}`).join('\n')
  const payload = t.knowledge_payload || {}
  knowledgeEdit.title = payload.title || t.title || ''
  knowledgeEdit.content = payload.content || ''
  knowledgeEdit.tags = (payload.tags || []).join(', ')
}

async function load(silent = false) {
  if (!silent) loading.value = true
  try {
    const data = await api.get(`/ops/tickets/${route.params.id}`)
    const changed = !ticket.value
    ticket.value = data
    if (changed) syncApprovalForm(data)
  } catch (e) {
    if (!silent) toast(e.message, 'error')
  } finally {
    loading.value = false
  }
}

async function loadLog() {
  try {
    const data = await api.get(`/ops/tickets/${route.params.id}/log`)
    logText.value = data.log || ''
    logResult.value = data.exec_result || null
    logTab.value = 'stdout'
  } catch (e) {
    toast(e.message, 'error')
  }
}

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

function buildExecEnv() {
  return {
    user: 'root',
    shell: 'bash',
    login: true,
    workdir: approval.workdir.trim() || '/root',
    timeout_seconds: Number(approval.timeout) || 3600,
    env: parseEnv(approval.envText),
  }
}

async function saveModify() {
  if (!ticket.value) return
  if (!approval.reason.trim()) { toast('管理员修改必须填写原因', 'error'); return }
  busy.value = 'modify'
  try {
    const body = { reason: approval.reason.trim() }
    if (ticket.value.kind === 'knowledge') {
      body.knowledge_payload = {
        ...(ticket.value.knowledge_payload || {}),
        title: knowledgeEdit.title.trim(),
        content: knowledgeEdit.content.trim(),
        tags: knowledgeEdit.tags.split(',').map(s => s.trim()).filter(Boolean),
      }
    } else {
      body.command = approval.command.trim()
      body.exec_env = buildExecEnv()
    }
    await api.put(`/ops/tickets/${ticket.value.id}`, body)
    toast('已保存修改', 'success')
    await load()
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    busy.value = ''
  }
}

async function approve(execute) {
  if (!ticket.value) return
  if (!approval.reason.trim()) { toast('批准必须填写原因', 'error'); return }
  busy.value = execute ? 'approve-exec' : 'approve'
  try {
    const body = { reason: approval.reason.trim(), execute }
    if (ticket.value.kind === 'knowledge') {
      body.knowledge_payload = {
        ...(ticket.value.knowledge_payload || {}),
        title: knowledgeEdit.title.trim(),
        content: knowledgeEdit.content.trim(),
        tags: knowledgeEdit.tags.split(',').map(s => s.trim()).filter(Boolean),
      }
    } else {
      body.command = approval.command.trim()
      body.exec_env = buildExecEnv()
      body.target_node_id = ticket.value.target_node_id
    }
    const data = await api.post(`/ops/tickets/${ticket.value.id}/approve`, body)
    ticket.value = data
    syncApprovalForm(data)
    toast(execute ? '已批准并开始执行' : '已批准', 'success')
    if (execute) startPolling()
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    busy.value = ''
  }
}

function openReject() {
  rejectReason.value = ''
  rejectOpen.value = true
}

async function reject() {
  if (!ticket.value) return
  if (!rejectReason.value.trim()) { toast('驳回必须填写原因', 'error'); return }
  busy.value = 'reject'
  try {
    ticket.value = await api.post(`/ops/tickets/${ticket.value.id}/reject`,
      { reason: rejectReason.value.trim() })
    rejectOpen.value = false
    toast('已驳回；系统会生成知识候选供管理员审核', 'success')
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    busy.value = ''
  }
}

async function execute() {
  if (!ticket.value) return
  if (!window.confirm('确认以 root 身份执行该已批准命令？单次授权只能执行一次。')) return
  busy.value = 'execute'
  try {
    ticket.value = await api.post(`/ops/tickets/${ticket.value.id}/execute`)
    toast('已提交执行', 'success')
    startPolling()
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    busy.value = ''
  }
}

async function submitDraft() {
  busy.value = 'submit'
  try {
    ticket.value = await api.post(`/ops/tickets/${ticket.value.id}/submit`)
    toast('已提交审批', 'success')
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    busy.value = ''
  }
}

async function cancelTicket() {
  if (!window.confirm('确定取消该工单？')) return
  busy.value = 'cancel'
  try {
    ticket.value = await api.post(`/ops/tickets/${ticket.value.id}/cancel`)
    toast('已取消', 'success')
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    busy.value = ''
  }
}

function downloadLog() {
  if (!logText.value) return
  downloadText(`neuops-${ticket.value.id}.log`, logText.value)
}

// ── 轮询执行状态 ────────────────────────────────────────────
let polling = null
function startPolling() {
  if (polling) return
  polling = setPolling(async () => {
    await load(true)
    const status = ticket.value?.status
    if (status !== 'executing' && status !== 'approved') {
      stopPolling()
      await loadLog()
    }
  }, 4000)
}
function stopPolling() {
  if (polling) { polling(); polling = null }
}

onMounted(async () => {
  await load()
  if (ticket.value && ['approved', 'executing'].includes(ticket.value.status)) {
    startPolling()
    loadLog()
  } else if (ticket.value?.exec_result?.worker_task_id) {
    loadLog()
  }
})
onBeforeUnmount(stopPolling)

const envEntries = computed(() =>
  Object.entries(ticket.value?.exec_env?.env || {}))
const showCommand = computed(() => ticket.value?.kind !== 'knowledge')
</script>

<template>
  <div>
    <div class="page-head">
      <button class="btn btn-ghost btn-sm" @click="router.push({ name: 'ops' })">
        <Icon name="chevron-right" :size="14" style="transform:rotate(180deg)" /> 返回工单
      </button>
      <div v-if="ticket" class="row" style="gap:8px;flex-wrap:wrap">
        <h1 class="page-title">{{ ticket.title || ticket.id }}</h1>
        <span class="badge" :class="statusClass(ticket.status)"><span class="dot" />{{ statusLabel(ticket.status) }}</span>
        <span class="badge neutral">{{ kindLabel(ticket.kind) }}</span>
        <span class="badge" :class="ticket.risk_level === 'critical' || ticket.risk_level === 'high' ? 'failed' : 'accent'">
          {{ riskLabel(ticket.risk_level) }}
        </span>
      </div>
      <div class="page-actions">
        <template v-if="ticket">
          <button v-if="ticket.can_execute && ticket.kind !== 'knowledge'" class="btn btn-primary" :disabled="!!busy" @click="execute">
            <Icon name="play" :size="14" /> 执行
          </button>
          <button v-if="ticket.status === 'draft' && (ticket.can_modify || isAdmin)"
                  class="btn btn-primary" :disabled="!!busy" @click="submitDraft">提交审批</button>
          <button v-if="['draft', 'pending'].includes(ticket.status) && (ticket.can_modify || isAdmin)"
                  class="btn btn-danger" :disabled="!!busy" @click="cancelTicket">取消</button>
        </template>
      </div>
    </div>

    <div v-if="loading && !ticket" class="empty-state">加载中…</div>

    <template v-if="ticket">
      <div class="ops-layout">
        <div>
          <div class="card">
            <div class="card-head"><span class="card-title">工单信息</span></div>
            <div class="card-body">
              <div class="ops-meta">
                <div><span class="k">工单 ID</span><span class="v mono">{{ ticket.id }}</span></div>
                <div><span class="k">目标节点</span><span class="v">{{ ticket.target_node_name || '—' }} <span class="mono text-3">{{ ticket.target_node_address }}</span></span></div>
                <div><span class="k">提交人</span><span class="v">{{ ticket.created_by || '—' }}</span></div>
                <div><span class="k">创建时间</span><span class="v">{{ formatTime(ticket.created_at) }}</span></div>
                <div><span class="k">提交时间</span><span class="v">{{ formatTime(ticket.submitted_at) }}</span></div>
                <div><span class="k">AI 模型</span><span class="v mono">{{ ticket.ai_model || '—' }}</span></div>
              </div>
              <div v-if="ticket.request" class="field" style="margin-top:16px">
                <label class="field-label">需求描述</label>
                <div class="ops-pre">{{ ticket.request }}</div>
              </div>
              <div v-if="showCommand" class="field">
                <label class="field-label">命令</label>
                <pre class="ops-pre mono">{{ ticket.command || '（尚未填写命令）' }}</pre>
              </div>
              <div v-if="showCommand" class="field">
                <label class="field-label">执行环境</label>
                <div class="ops-pre mono">
                  user={{ ticket.exec_env?.user || 'root' }} shell={{ ticket.exec_env?.shell || 'bash' }}
                  login={{ ticket.exec_env?.login }} workdir={{ ticket.exec_env?.workdir || '/root' }}
                  timeout={{ ticket.exec_env?.timeout_seconds || 3600 }}s
                  <span v-for="[k, v] in envEntries" :key="k"> {{ k }}={{ v }}</span>
                </div>
              </div>
              <div v-if="showCommand" class="field">
                <label class="field-label">资源参数</label>
                <div class="ops-pre mono">{{ JSON.stringify(ticket.params || {}) }}</div>
              </div>
            </div>
          </div>

          <div v-if="ticket.kind === 'knowledge'" class="card">
            <div class="card-head"><span class="card-title">知识候选内容</span></div>
            <div class="card-body">
              <div class="field">
                <label class="field-label">标题</label>
                <div class="ops-pre">{{ ticket.knowledge_payload?.title || ticket.title }}</div>
              </div>
              <div class="field">
                <label class="field-label">内容</label>
                <div class="ops-pre">{{ ticket.knowledge_payload?.content || '—' }}</div>
              </div>
              <div class="field">
                <label class="field-label">标签</label>
                <div class="row wrap">
                  <span v-for="tag in ticket.knowledge_payload?.tags || []" :key="tag" class="tag">{{ tag }}</span>
                  <span v-if="!ticket.knowledge_payload?.tags?.length" class="text-3">无</span>
                </div>
              </div>
            </div>
          </div>

          <div v-if="ticket.ai_suggestion && Object.keys(ticket.ai_suggestion).length" class="card">
            <div class="card-head"><span class="card-title">AI 命令建议（历史）</span></div>
            <div class="card-body">
              <p style="white-space:pre-wrap;margin-top:0">{{ ticket.ai_suggestion.reply || '—' }}</p>
              <div v-if="ticket.ai_suggestion.explanation" class="field">
                <label class="field-label">说明与风险</label>
                <div class="ops-pre">{{ ticket.ai_suggestion.explanation }}</div>
              </div>
              <div v-if="ticket.ai_suggestion.risk_notes?.length" class="field">
                <label class="field-label">风险提示</label>
                <ul style="margin:0;padding-left:20px">
                  <li v-for="note in ticket.ai_suggestion.risk_notes" :key="note">{{ note }}</li>
                </ul>
              </div>
              <div v-if="ticket.ai_suggestion.command" class="field">
                <label class="field-label">AI 推荐命令</label>
                <pre class="ops-pre mono">{{ ticket.ai_suggestion.command }}</pre>
              </div>
              <div v-if="ticket.ai_suggestion.alternatives?.length" class="field">
                <label class="field-label">备选方案</label>
                <ul style="margin:0;padding-left:20px">
                  <li v-for="alt in ticket.ai_suggestion.alternatives" :key="alt">{{ alt }}</li>
                </ul>
              </div>
            </div>
          </div>

          <div v-if="ticket.approval_reason || ticket.reject_reason || ticket.admin_note" class="card">
            <div class="card-head"><span class="card-title">审批意见</span></div>
            <div class="card-body">
              <div v-if="ticket.approval_reason" class="field">
                <label class="field-label">批准原因</label>
                <div class="ops-pre">{{ ticket.approval_reason }}</div>
              </div>
              <div v-if="ticket.reject_reason" class="field">
                <label class="field-label">驳回原因</label>
                <div class="ops-pre">{{ ticket.reject_reason }}</div>
              </div>
              <div v-if="ticket.admin_note && ticket.admin_note !== (ticket.approval_reason || ticket.reject_reason)" class="field">
                <label class="field-label">管理员备注</label>
                <div class="ops-pre">{{ ticket.admin_note }}</div>
              </div>
            </div>
          </div>

          <div v-if="ticket.exec_result && Object.keys(ticket.exec_result).length" class="card">
            <div class="card-head">
              <span class="card-title">执行结果</span>
              <span class="spacer" />
              <button class="btn btn-sm" @click="loadLog"><Icon name="eye" :size="13" /> 加载完整日志</button>
              <button class="btn btn-sm" :disabled="!logText" @click="downloadLog"><Icon name="download" :size="13" /> 下载</button>
            </div>
            <div class="card-body">
              <div class="ops-meta">
                <div><span class="k">退出码</span><span class="v mono">{{ ticket.exec_result.returncode }}</span></div>
                <div><span class="k">超时</span><span class="v">{{ ticket.exec_result.timed_out ? '是' : '否' }}</span></div>
                <div><span class="k">耗时</span><span class="v mono">{{ ticket.exec_result.duration_seconds }}s</span></div>
                <div><span class="k">worker 任务</span><span class="v mono">{{ ticket.exec_result.worker_task_id || '—' }}</span></div>
                <div><span class="k">错误</span><span class="v">{{ ticket.exec_result.error || '—' }}</span></div>
              </div>
              <div class="tabs" style="margin-top:12px">
                <button class="tab" :class="{ active: logTab === 'stdout' }" @click="logTab = 'stdout'">stdout</button>
                <button class="tab" :class="{ active: logTab === 'stderr' }" @click="logTab = 'stderr'">stderr</button>
                <button class="tab" :class="{ active: logTab === 'log' }" @click="logTab = 'log'; !logText && loadLog()">完整日志</button>
              </div>
              <pre v-if="logTab === 'stdout'" class="ops-log">{{ ticket.exec_result.stdout || '（空）' }}</pre>
              <pre v-else-if="logTab === 'stderr'" class="ops-log">{{ ticket.exec_result.stderr || '（空）' }}</pre>
              <pre v-else class="ops-log">{{ logText || '（尚未加载）' }}</pre>
            </div>
          </div>

          <div class="card">
            <div class="card-head"><span class="card-title">事件时间线</span></div>
            <div class="card-body">
              <div v-if="!ticket.events?.length" class="text-3">暂无事件</div>
              <div v-for="event in ticket.events" :key="event.id" class="ops-event">
                <div class="ops-event-dot" />
                <div class="grow">
                  <div class="row" style="gap:8px">
                    <strong>{{ event.action }}</strong>
                    <span class="text-3">{{ event.actor }}</span>
                    <span class="spacer" />
                    <span class="text-3">{{ formatTimeShort(event.created_at) }}</span>
                  </div>
                  <div v-if="event.detail && Object.keys(event.detail).length" class="ops-pre mono" style="margin-top:4px">{{ JSON.stringify(event.detail) }}</div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 审批侧栏 -->
        <div>
          <div v-if="isAdmin && ticket.status === 'pending'" class="card">
            <div class="card-head"><span class="card-title">管理员审批</span></div>
            <div class="card-body">
              <div v-if="ticket.kind === 'knowledge'">
                <div class="field">
                  <label class="field-label">知识标题</label>
                  <input v-model="knowledgeEdit.title" class="input">
                </div>
                <div class="field">
                  <label class="field-label">知识内容</label>
                  <textarea v-model="knowledgeEdit.content" class="textarea" rows="8" />
                </div>
                <div class="field">
                  <label class="field-label">标签（逗号分隔）</label>
                  <input v-model="knowledgeEdit.tags" class="input">
                </div>
              </div>
              <template v-else>
                <div class="field">
                  <label class="field-label">最终命令（完整、不可在批准后修改）</label>
                  <textarea v-model="approval.command" class="textarea mono" rows="7" />
                </div>
                <div class="input-row">
                  <div class="field">
                    <label class="field-label">工作目录</label>
                    <input v-model="approval.workdir" class="input mono">
                  </div>
                  <div class="field">
                    <label class="field-label">超时（秒）</label>
                    <input v-model="approval.timeout" class="input mono">
                  </div>
                </div>
                <div class="field">
                  <label class="field-label">环境变量（每行 KEY=value）</label>
                  <textarea v-model="approval.envText" class="textarea mono" rows="3" />
                </div>
              </template>
              <div class="field">
                <label class="field-label">原因（必填）</label>
                <textarea v-model="approval.reason" class="textarea" rows="3"
                          placeholder="批准/修改的原因、风险判断或回滚说明" />
              </div>
              <div class="row wrap">
                <button v-if="ticket.kind !== 'knowledge'" class="btn btn-primary"
                        :disabled="!!busy" @click="approve(true)">
                  <Icon name="play" :size="14" /> 批准并执行
                </button>
                <button class="btn" :disabled="!!busy" @click="approve(false)">
                  <Icon name="check" :size="14" /> {{ ticket.kind === 'knowledge' ? '批准入库' : '仅批准' }}
                </button>
                <button class="btn" :disabled="!!busy" @click="saveModify">保存修改</button>
                <button class="btn btn-danger" :disabled="!!busy" @click="openReject">驳回</button>
              </div>
            </div>
          </div>

          <div v-else-if="isAdmin && ticket.status === 'approved' && ticket.kind !== 'knowledge'" class="card">
            <div class="card-head"><span class="card-title">待执行</span></div>
            <div class="card-body">
              <p class="text-3" style="margin-top:0">审批快照已冻结。确认后会消费一次性授权，仅执行一次。</p>
              <button class="btn btn-primary" :disabled="!!busy" @click="execute">
                <Icon name="play" :size="14" /> 以 root 执行
              </button>
            </div>
          </div>

          <div v-else class="card">
            <div class="card-head"><span class="card-title">状态</span></div>
            <div class="card-body">
              <p style="margin-top:0">当前状态：<strong>{{ statusLabel(ticket.status) }}</strong></p>
              <p v-if="ticket.status === 'executing'" class="text-3">执行器中，页面会自动刷新。</p>
              <p v-else-if="ticket.status === 'draft'" class="text-3">草稿需提交给管理员审批。</p>
              <p v-else-if="ticket.status === 'rejected'" class="text-3">已驳回；系统会从驳回原因中生成知识候选。</p>
              <p v-else-if="ticket.status === 'succeeded'" class="text-3">执行成功，结果已写入工单与完整日志。</p>
              <p v-else-if="ticket.status === 'failed'" class="text-3">执行失败，请查看 stderr 与完整日志。</p>
              <router-link class="btn btn-sm" to="/ops/knowledge">查看知识库</router-link>
            </div>
          </div>
        </div>
      </div>
    </template>

    <Modal v-model="rejectOpen" title="驳回工单">
      <div class="field">
        <label class="field-label">驳回原因（必填）</label>
        <textarea v-model="rejectReason" class="textarea" rows="4"
                  placeholder="例如：命令影响范围过大，请先备份并改为精确路径" />
        <div class="field-hint">驳回原因会作为 AI 自学习的重要证据，并生成待审批的知识候选。</div>
      </div>
      <template #footer>
        <button class="btn" @click="rejectOpen = false">取消</button>
        <button class="btn btn-danger solid" :disabled="!!busy" @click="reject">确认驳回</button>
      </template>
    </Modal>
  </div>
</template>

<style scoped>
.ops-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  gap: 16px;
  align-items: start;
}
@media (max-width: 1100px) {
  .ops-layout { grid-template-columns: 1fr; }
}
.ops-meta {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 20px;
}
.ops-meta .k { display: block; font-size: 11.5px; color: var(--text-3); }
.ops-meta .v { font-size: 12.5px; word-break: break-all; }
.ops-pre {
  white-space: pre-wrap;
  word-break: break-word;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 10px 12px;
  font-size: 12.5px;
  line-height: 1.6;
}
.ops-log {
  margin: 12px 0 0;
  max-height: 420px;
  overflow: auto;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 12px;
  font-family: var(--mono);
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
}
.ops-event {
  display: flex;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px solid var(--border);
}
.ops-event:last-child { border-bottom: none; }
.ops-event-dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--accent); margin-top: 7px; flex: none;
}
</style>
