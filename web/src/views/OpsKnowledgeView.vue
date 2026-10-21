<script setup>
/**
 * 知识库：管理员批准后的经验/runbook/约束列表。
 * 新知识必须先作为 knowledge 工单提交，管理员审批后才会出现在这里。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import Icon from '../components/Icon.vue'
import Modal from '../components/Modal.vue'
import { api } from '../api'
import { authRO, toast } from '../store'
import { formatTime } from '../utils'

const router = useRouter()
const user = computed(() => authRO.user)
const isAdmin = computed(() => user.value?.role === 'admin')

const knowledge = ref([])
const pending = ref([])
const search = ref('')
const kind = ref('')
const loading = ref(false)
const selected = ref(null)

const kindMeta = {
  experience: '经验', runbook: '操作手册',
  constraint: '约束', faq: '常见问题',
}
function kindLabel(value) { return kindMeta[value] || value || '经验' }

let searchTimer = null
function onSearch() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(loadKnowledge, 300)
}

async function loadKnowledge() {
  loading.value = true
  try {
    const params = new URLSearchParams()
    if (search.value.trim()) params.set('q', search.value.trim())
    if (kind.value) params.set('kind', kind.value)
    const data = await api.get(`/ops/knowledge?${params}`)
    knowledge.value = data.knowledge || []
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    loading.value = false
  }
}

async function loadPending() {
  if (!isAdmin.value) return
  try {
    const data = await api.get('/ops/tickets?kind=knowledge&status=pending&limit=50')
    pending.value = data.tickets || []
  } catch { /* 忽略 */ }
}

async function reload() {
  await Promise.all([loadKnowledge(), loadPending()])
}

onMounted(reload)

// ── 手动触发学习 ────────────────────────────────────────────
const learning = ref(false)
async function learn() {
  learning.value = true
  try {
    const data = await api.post('/ops/knowledge/learn', {})
    toast(`已生成 ${data.count || 0} 条知识候选，等待审批`, 'success')
    await reload()
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    learning.value = false
  }
}

// ── 新建知识候选 ────────────────────────────────────────────
const createOpen = ref(false)
const createBusy = ref(false)
const form = reactive({
  title: '', content: '', kind: 'experience', tags: '',
})
function openCreate() {
  form.title = ''
  form.content = ''
  form.kind = 'experience'
  form.tags = ''
  createOpen.value = true
}
async function submitCreate() {
  if (!form.title.trim() || !form.content.trim()) {
    toast('标题和内容不能为空', 'error')
    return
  }
  createBusy.value = true
  try {
    await api.post('/ops/tickets', {
      kind: 'knowledge',
      status: 'pending',
      title: form.title.trim(),
      knowledge_payload: {
        title: form.title.trim(),
        content: form.content.trim(),
        kind: form.kind,
        tags: form.tags.split(',').map(s => s.trim()).filter(Boolean),
      },
    })
    createOpen.value = false
    toast('知识候选已提交，等待管理员审批', 'success')
    await reload()
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
        <h1 class="page-title">运维知识库</h1>
        <div class="page-desc">从真实工单、审批意见、驳回原因和执行结果中提炼；新增条目必须先走工单审批。</div>
      </div>
      <div class="page-actions">
        <button v-if="isAdmin" class="btn" :disabled="learning" @click="learn">
          <Icon name="refresh" :size="14" :class="{ spin: learning }" /> 从历史工单学习
        </button>
        <button class="btn btn-primary" @click="openCreate">
          <Icon name="plus" :size="14" /> 新建知识候选
        </button>
      </div>
    </div>

    <div class="card">
      <div class="card-head">
        <div class="row grow" style="gap:8px;flex-wrap:wrap">
          <div class="row" style="gap:6px">
            <Icon name="search" :size="14" class="text-3" />
            <input v-model="search" class="input" style="width:260px;padding:5px 10px;font-size:12.5px"
                   placeholder="FTS5 搜索标题 / 内容 / 标签…" @input="onSearch">
          </div>
          <select v-model="kind" class="select" style="width:140px;padding:5px 9px;font-size:12.5px"
                  @change="loadKnowledge">
            <option value="">全部类型</option>
            <option value="experience">经验</option>
            <option value="runbook">操作手册</option>
            <option value="constraint">约束</option>
            <option value="faq">常见问题</option>
          </select>
        </div>
        <span class="card-sub">{{ knowledge.length }} 条</span>
        <button class="btn btn-ghost btn-icon" title="刷新" @click="reload">
          <Icon name="refresh" :size="14" :class="{ spin: loading }" />
        </button>
      </div>
      <div class="card-body flush">
        <div v-if="!knowledge.length" class="empty-state">
          <div class="icon">📚</div>
          <p>知识库还是空的。执行/驳回工单后会自动生成候选，管理员批准后入库。</p>
        </div>
        <div v-for="item in knowledge" :key="item.id" class="kb-card" @click="selected = item">
          <div class="row" style="gap:8px;flex-wrap:wrap">
            <strong>{{ item.title }}</strong>
            <span class="badge accent">{{ kindLabel(item.kind) }}</span>
            <span v-for="tag in item.tags || []" :key="tag" class="tag">{{ tag }}</span>
            <span class="spacer" />
            <span class="text-3">{{ formatTime(item.approved_at) }}</span>
          </div>
          <div class="kb-content">{{ item.content }}</div>
          <div class="cell-sub">
            来源工单：
            <span v-for="sid in item.source_ticket_ids || []" :key="sid" class="mono">{{ sid }} </span>
            <span v-if="!item.source_ticket_ids?.length">—</span>
            · 批准人：{{ item.approved_by || '—' }}
          </div>
        </div>
      </div>
    </div>

    <div v-if="isAdmin" class="card">
      <div class="card-head">
        <span class="card-title">待审批知识候选</span>
        <span class="spacer" />
        <span class="card-sub">{{ pending.length }} 条</span>
      </div>
      <div class="card-body flush">
        <div v-if="!pending.length" class="empty-state" style="padding:24px">暂无待审批知识候选</div>
        <div v-for="item in pending" :key="item.id" class="queue-row clickable"
             @click="router.push({ name: 'ops-ticket', params: { id: item.id } })">
          <div class="grow">
            <div class="cell-main">{{ item.title || item.id }}</div>
            <div class="cell-sub">来源工单 {{ item.source_ticket_id || '—' }} · {{ formatTime(item.created_at) }}</div>
          </div>
          <span class="badge queued"><span class="dot" />待审批</span>
          <Icon name="chevron-right" :size="14" class="text-3" />
        </div>
      </div>
    </div>

    <!-- 知识详情 -->
    <Modal :model-value="!!selected" title="知识条目" wide @update:model-value="selected = null">
      <template v-if="selected">
        <h3 style="margin-top:0">{{ selected.title }}</h3>
        <div class="row wrap" style="margin-bottom:12px">
          <span class="badge accent">{{ kindLabel(selected.kind) }}</span>
          <span v-for="tag in selected.tags || []" :key="tag" class="tag">{{ tag }}</span>
        </div>
        <div class="ops-pre">{{ selected.content }}</div>
        <div class="cell-sub" style="margin-top:12px">
          来源工单：{{ (selected.source_ticket_ids || []).join(', ') || '—' }} ·
          批准人：{{ selected.approved_by || '—' }} ·
          {{ formatTime(selected.approved_at) }}
        </div>
      </template>
    </Modal>

    <!-- 新建知识候选 -->
    <Modal v-model="createOpen" title="新建知识候选" wide>
      <div class="field">
        <label class="field-label">标题</label>
        <input v-model="form.title" class="input" placeholder="例如：GPU 节点 /var 清理的推荐步骤">
      </div>
      <div class="input-row">
        <div class="field">
          <label class="field-label">类型</label>
          <select v-model="form.kind" class="select">
            <option value="experience">经验</option>
            <option value="runbook">操作手册</option>
            <option value="constraint">约束</option>
            <option value="faq">常见问题</option>
          </select>
        </div>
        <div class="field">
          <label class="field-label">标签（逗号分隔）</label>
          <input v-model="form.tags" class="input" placeholder="磁盘, 清理, 安全">
        </div>
      </div>
      <div class="field">
        <label class="field-label">内容</label>
        <textarea v-model="form.content" class="textarea" rows="8"
                  placeholder="适用场景、操作步骤、风险与回滚方式…" />
      </div>
      <div class="field-hint">
        该条目会以 knowledge 工单形式进入审批列表；管理员批准后才会出现在知识库。
      </div>
      <template #footer>
        <button class="btn" @click="createOpen = false">取消</button>
        <button class="btn btn-primary" :disabled="createBusy" @click="submitCreate">提交审批</button>
      </template>
    </Modal>
  </div>
</template>

<style scoped>
.kb-card {
  padding: 14px 16px;
  border-bottom: 1px solid var(--border);
  cursor: pointer;
  transition: background .1s;
}
.kb-card:last-child { border-bottom: none; }
.kb-card:hover { background: var(--surface-2); }
.kb-content {
  margin: 8px 0;
  color: var(--text-2);
  font-size: 13px;
  white-space: pre-wrap;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.ops-pre {
  white-space: pre-wrap;
  word-break: break-word;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 10px 12px;
  font-size: 12.5px;
  line-height: 1.6;
  max-height: 420px;
  overflow: auto;
}
</style>
