<script setup>
/**
 * 实验详情（notebook）：
 *  - 标题/标签/文件夹 + 预览|编辑 两种模式
 *  - text block: markdown（编辑时支持粘贴图片上传）
 *  - task block: 命令 + 任务引用（懒加载日志）/ 内联日志
 *  - 保存（Ctrl+S）/ 导出 Markdown / 删除
 *  - id = 'new' 时为空实验，首次保存时创建
 */
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { marked } from 'marked'
import { useRouter, useRoute } from 'vue-router'
import Icon from '../components/Icon.vue'
import Modal from '../components/Modal.vue'
import { api } from '../api'
import { authRO, toast } from '../store'
import { downloadText, safeFilename, formatTime, handleCR } from '../utils'

const router = useRouter()
const route = useRoute()
const user = computed(() => authRO.user)

const isNew = computed(() => route.params.id === 'new')
const exp = ref(null)
const blocks = ref([])
const title = ref('')
const tagsRaw = ref('')
const folderId = ref('')
const folders = ref([])
const mode = ref('preview')
const dirty = ref(false)
const saving = ref(false)
const notFound = ref(false)

const canModify = computed(() => {
  if (!exp.value || isNew.value) return true
  if (user.value?.role === 'admin') return true
  return !!exp.value.created_by && exp.value.created_by === user.value?.username
})

const tags = computed(() =>
  tagsRaw.value.split(/[,，]/).map(s => s.trim()).filter(Boolean))

// ── 数据加载 ────────────────────────────────────────────────
async function load() {
  if (isNew.value) {
    exp.value = null
    blocks.value = [{ type: 'text', content: '' }]
    title.value = ''
    tagsRaw.value = ''
    folderId.value = ''
    mode.value = 'edit'
    return
  }
  try {
    const data = await api.get(`/experiments/${route.params.id}`)
    exp.value = data
    blocks.value = JSON.parse(JSON.stringify(data.blocks || []))
    title.value = data.title || ''
    tagsRaw.value = (data.tags || []).join(', ')
    folderId.value = data.folder_id || ''
  } catch (e) {
    notFound.value = e.status === 404
    if (!notFound.value) toast(e.message, 'error')
  }
}

async function loadFolders() {
  try {
    const data = await api.get('/experiments/folders')
    folders.value = data.folders || []
  } catch { folders.value = [] }
}
function flattenFolders(items, depth = 0, out = []) {
  for (const f of items || []) {
    out.push({ id: f.id, label: `${'　'.repeat(depth)}📁 ${f.name}` })
    flattenFolders(f.children, depth + 1, out)
  }
  return out
}
const folderOptions = computed(() => flattenFolders(folders.value))

// ── 块操作 ──────────────────────────────────────────────────
function insertBlock(pos, type) {
  if (type === 'text') blocks.value.splice(pos, 0, { type: 'text', content: '' })
  else openTaskPicker(pos)
  dirty.value = true
}
function removeBlock(idx) {
  blocks.value.splice(idx, 1)
  dirty.value = true
}

function md(text) {
  if (!text || !text.trim()) return '<p class="text-3" style="font-style:italic">空笔记</p>'
  return marked.parse(text)
}

// ── 任务块日志 ──────────────────────────────────────────────
function hasLog(block) {
  return !!(block.task_id || (block.log && block.log.trim()))
}
function toggleLog(block) {
  if (!hasLog(block)) return
  block.logOpen = !block.logOpen
  if (block.logOpen && block.task_id && !block.log) loadBlockLog(block)
}
async function loadBlockLog(block) {
  if (!block.task_id) return
  // 1) master 缓存
  try {
    const r = await fetch(`/experiments/log/${block.task_id}`)
    if (r.ok) {
      block.log = handleCR(await r.text())
      block.logLoaded = true
      return
    }
  } catch { /* 继续 */ }
  // 2) worker
  if (block.node_id) {
    try {
      const r = await fetch(
        `/tasks/${block.task_id}/log?node_id=${encodeURIComponent(block.node_id)}&raw=1`)
      if (r.ok) {
        block.log = handleCR(await r.text())
        block.logLoaded = true
        return
      }
    } catch { /* 继续 */ }
  }
  toast('日志加载失败（任务可能已删除）', 'error')
}

// ── 粘贴图片上传 ────────────────────────────────────────────
function onPaste(e, block) {
  const items = e.clipboardData && e.clipboardData.items
  if (!items) return
  for (const item of items) {
    if (!item.type.startsWith('image/')) continue
    e.preventDefault()
    const file = item.getAsFile()
    if (!file) continue
    const ta = e.target
    const placeholder = '![上传中…]()'
    const start = ta.selectionStart
    const end = ta.selectionEnd
    block.content = String(block.content || '').substring(0, start) +
      placeholder + String(block.content || '').substring(end)
    nextTick(() => {
      ta.selectionStart = ta.selectionEnd = start + placeholder.length
    })
    const form = new FormData()
    form.append('file', file)
    api.post('/experiments/upload-image', form).then(d => {
      block.content = String(block.content || '').replace(placeholder, `![image](${d.url})`)
      dirty.value = true
      toast('图片已上传', 'success')
    }).catch(err => {
      block.content = String(block.content || '').replace(placeholder, '')
      toast(err.message || '上传失败', 'error')
    })
    return
  }
}

// ── 任务选择器 ──────────────────────────────────────────────
const pickerOpen = ref(false)
const pickerPos = ref(0)
const pickerTab = ref('link')
const picker = reactive({ taskId: '', nodeId: '', cmd: '', log: '', loading: false, result: '' })
const nodeOptions = ref([])

async function loadNodeOptions() {
  try {
    const d = await api.post('/nodes/get_all_nodes', {})
    nodeOptions.value = d.nodes || []
  } catch { /* 忽略 */ }
}

function openTaskPicker(pos) {
  pickerPos.value = pos
  pickerTab.value = 'link'
  picker.taskId = ''
  picker.nodeId = ''
  picker.cmd = ''
  picker.log = ''
  picker.result = ''
  pickerOpen.value = true
}

async function pickerFetch() {
  if (!picker.taskId.trim()) { picker.result = '请输入 task_id'; return }
  if (!picker.nodeId) { picker.result = '请选择节点'; return }
  picker.loading = true
  picker.result = ''
  try {
    const t = await api.get(
      `/tasks/${encodeURIComponent(picker.taskId.trim())}?node_id=${encodeURIComponent(picker.nodeId)}`)
    if (t.error) throw new Error(t.error)
    blocks.value.splice(pickerPos.value, 0, {
      type: 'task',
      command: t.command || '',
      task_id: t.task_id || picker.taskId.trim(),
      node_id: picker.nodeId,
    })
    dirty.value = true
    pickerOpen.value = false
    toast('已关联任务', 'success')
  } catch (e) {
    picker.result = e.message
  } finally {
    picker.loading = false
  }
}

function pickerInsertManual() {
  blocks.value.splice(pickerPos.value, 0, {
    type: 'task',
    command: picker.cmd,
    log: picker.log,
  })
  dirty.value = true
  pickerOpen.value = false
}

// ── 保存 / 导出 / 删除 ──────────────────────────────────────
async function save() {
  const t = title.value.trim()
  if (!t) { toast('标题不能为空', 'error'); return }
  saving.value = true
  try {
    const logs = {}
    for (const b of blocks.value) {
      if (b.type === 'task' && b.task_id && b.log) logs[b.task_id] = b.log
    }
    const payload = {
      title: t,
      blocks: blocks.value,
      tags: tags.value,
      logs,
      folder_id: folderId.value || null,
    }
    if (isNew.value) {
      const d = await api.post('/experiments/', payload)
      toast('实验记录已创建', 'success')
      router.replace({ name: 'experiment-detail', params: { id: d.id } })
      await load()
    } else {
      await api.put(`/experiments/${route.params.id}`, payload)
      exp.value.title = t
      exp.value.tags = tags.value
      toast('已保存', 'success')
    }
    dirty.value = false
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    saving.value = false
  }
}

function exportMd() {
  const lines = []
  const t = title.value || exp.value?.title || ''
  lines.push(`# ${t}`, '')
  const meta = []
  if (exp.value?.created_by) meta.push(`创建者: ${exp.value.created_by}`)
  if (exp.value?.updated_at) meta.push(`更新: ${formatTime(exp.value.updated_at)}`)
  if (tags.value.length) meta.push(`标签: ${tags.value.join(', ')}`)
  if (meta.length) lines.push(`> ${meta.join(' | ')}`, '')
  blocks.value.forEach((block, i) => {
    lines.push('---', '')
    if (block.type === 'text') {
      lines.push(block.content || '', '')
    } else if (block.type === 'task') {
      lines.push(`### 任务 ${i + 1}: \`${block.command || '未命名'}\``, '')
      if (block.log && block.log.trim()) lines.push('```bash', block.log, '```', '')
    }
  })
  downloadText(`${safeFilename(t)}.md`, lines.join('\n'), 'text/markdown')
  toast('已导出', 'success')
}

async function removeExperiment() {
  if (!window.confirm('确定删除这个实验？此操作不可恢复。')) return
  try {
    await api.delete(`/experiments/${route.params.id}`)
    toast('已删除', 'success')
    router.push({ name: 'experiments' })
  } catch (e) {
    toast(e.message, 'error')
  }
}

// ── Ctrl+S ──────────────────────────────────────────────────
function onKeydown(e) {
  if ((e.ctrlKey || e.metaKey) && e.key === 's') {
    e.preventDefault()
    if (canModify.value && mode.value === 'edit') save()
  }
}

onMounted(() => {
  document.addEventListener('keydown', onKeydown)
  load()
  loadFolders()
  loadNodeOptions()
})
onBeforeUnmount(() => document.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h1 class="page-title">实验详情</h1>
        <div class="page-desc">{{ isNew ? '新建实验' : (exp?.title || '加载中…') }}</div>
      </div>
      <div class="page-actions">
        <router-link class="btn btn-ghost" to="/experiments">← 返回列表</router-link>
      </div>
    </div>

    <div v-if="notFound" class="card">
      <div class="empty-state">
        <div class="icon">🔍</div>
        <p>实验不存在或已删除</p>
      </div>
    </div>

    <div v-else class="nb" :class="{ editing: mode === 'edit' }">
      <div class="nb-head">
        <input v-model="title" class="nb-title-input"
               :readonly="!canModify || mode !== 'edit'"
               placeholder="实验标题">

        <div class="nb-meta-row">
          <span v-if="exp?.created_by">创建者: {{ exp.created_by }}</span>
          <span v-if="exp?.updated_at">更新: {{ formatTime(exp.updated_at) }}</span>
          <span v-if="dirty" class="text-danger">· 未保存的更改</span>
          <span class="grow" />
          <div v-if="canModify" class="tabs">
            <button class="tab" :class="{ active: mode === 'preview' }"
                    @click="mode = 'preview'">预览</button>
            <button class="tab" :class="{ active: mode === 'edit' }"
                    @click="mode = 'edit'">编辑</button>
          </div>
          <template v-if="canModify && mode === 'edit'">
            <button class="btn btn-primary btn-sm" :disabled="saving" @click="save">
              {{ saving ? '保存中…' : '保存' }}
            </button>
            <button v-if="!isNew" class="btn btn-danger btn-sm" @click="removeExperiment">
              <Icon name="trash" :size="13" /> 删除
            </button>
          </template>
          <button class="btn btn-sm" @click="exportMd">
            <Icon name="download" :size="13" /> 导出
          </button>
        </div>

        <div v-if="canModify && mode === 'edit'" class="input-row mt-8" style="max-width:640px">
          <div class="field grow" style="margin:0">
            <label class="field-label">标签 <span class="field-hint">逗号分隔</span></label>
            <input v-model="tagsRaw" class="input" placeholder="breakdown, resnet50">
          </div>
          <div class="field grow" style="margin:0">
            <label class="field-label">文件夹</label>
            <select v-model="folderId" class="select">
              <option value="">— 无 —</option>
              <option v-for="f in folderOptions" :key="f.id" :value="f.id">{{ f.label }}</option>
            </select>
          </div>
        </div>
        <div v-else-if="(exp?.tags || []).length" class="row mt-8 wrap">
          <span v-for="t in exp.tags" :key="t" class="tag">{{ t }}</span>
        </div>
      </div>

      <!-- 块 -->
      <div class="nb-blocks">
        <template v-for="(block, i) in blocks" :key="'b' + i">
          <div v-if="canModify && mode === 'edit'" class="nb-insert-bar">
            <button class="nb-ins-btn" @click="insertBlock(i, 'text')">+ 笔记</button>
            <button class="nb-ins-btn" @click="openTaskPicker(i)">+ 任务</button>
          </div>

          <div class="nb-block">
            <button v-if="canModify && mode === 'edit'" class="nb-del"
                    title="删除此块" @click="removeBlock(i)">×</button>

            <!-- text block -->
            <template v-if="block.type === 'text'">
              <div v-if="mode === 'preview'" class="nb-md" v-html="md(block.content)" />
              <textarea v-else class="nb-textarea" v-model="block.content"
                        @input="dirty = true" @paste="onPaste($event, block)"
                        placeholder="写笔记…（支持 markdown，可直接粘贴图片）" />
            </template>

            <!-- task block -->
            <template v-else-if="block.type === 'task'">
              <div class="nb-task-cmd" @click="toggleLog(block)">
                <span class="arrow" :class="{ expanded: block.logOpen }"
                      :style="{ visibility: hasLog(block) ? 'visible' : 'hidden' }">▶</span>
                <span class="t-cmd">{{ block.command || '无命令' }}</span>
                <span v-if="block.task_id" class="t-ref">
                  {{ block.task_id }} @ {{ block.node_id || '?' }}
                </span>
              </div>
              <template v-if="block.logOpen && hasLog(block)">
                <textarea v-if="mode === 'edit'" class="nb-task-log-input"
                          v-model="block.log" rows="8" @input="dirty = true" />
                <pre v-else class="nb-task-log">{{ block.log || '(无日志)' }}</pre>
              </template>
            </template>
          </div>
        </template>

        <div v-if="canModify && mode === 'edit'" class="nb-insert-bar">
          <button class="nb-ins-btn" @click="insertBlock(blocks.length, 'text')">+ 笔记</button>
          <button class="nb-ins-btn" @click="openTaskPicker(blocks.length)">+ 任务</button>
        </div>

        <div v-if="!blocks.length && mode === 'preview'" class="empty-state">
          <div class="icon">📝</div>
          <p>暂无内容，切换到编辑模式添加块</p>
        </div>
      </div>
    </div>

    <!-- 任务选择器 -->
    <Modal v-model="pickerOpen" title="添加任务块">
      <div class="tabs mb-16">
        <button class="tab" :class="{ active: pickerTab === 'link' }"
                @click="pickerTab = 'link'">关联已有任务</button>
        <button class="tab" :class="{ active: pickerTab === 'manual' }"
                @click="pickerTab = 'manual'">手动输入</button>
      </div>

      <div v-if="pickerTab === 'link'">
        <div class="input-row">
          <div class="field grow">
            <label class="field-label">任务 ID <span class="field-hint">worker 上的 task_id</span></label>
            <input v-model="picker.taskId" class="input mono" placeholder="task_xxx">
          </div>
          <div class="field grow">
            <label class="field-label">节点</label>
            <select v-model="picker.nodeId" class="select">
              <option value="">— 选择 —</option>
              <option v-for="n in nodeOptions" :key="n.node_id" :value="n.node_id">
                {{ n.name }}
              </option>
            </select>
          </div>
        </div>
        <div v-if="picker.result" class="login-error">{{ picker.result }}</div>
        <div class="row mt-8">
          <button class="btn btn-primary btn-sm" :disabled="picker.loading" @click="pickerFetch">
            {{ picker.loading ? '获取中…' : '获取并插入' }}
          </button>
        </div>
      </div>

      <div v-else>
        <div class="field">
          <label class="field-label">命令</label>
          <input v-model="picker.cmd" class="input mono" placeholder="python train.py">
        </div>
        <div class="field">
          <label class="field-label">日志输出</label>
          <textarea v-model="picker.log" class="textarea mono" rows="5"></textarea>
        </div>
        <div class="row mt-8">
          <button class="btn btn-primary btn-sm" @click="pickerInsertManual">插入</button>
        </div>
      </div>
    </Modal>
  </div>
</template>
