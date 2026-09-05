<script setup>
/**
 * 任务页：
 *   左栏  节点列表（选择/设备/沙盒）
 *   中栏  提交表单 + 队列（我的队列 / 全部队列 两个 tab）
 *   右栏  日志查看器（选中任务时显示）
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import Icon from '../components/Icon.vue'
import Modal from '../components/Modal.vue'
import NodeList from '../components/NodeList.vue'
import QueueList from '../components/QueueList.vue'
import Stepper from '../components/Stepper.vue'
import TaskLog from '../components/TaskLog.vue'
import { api } from '../api'
import { authRO, toast } from '../store'
import { joinCommandLines, memToGB, setPolling, statusLabel } from '../utils'

const user = computed(() => authRO.user)

// ── 节点 ────────────────────────────────────────────────────
const nodes = ref([])
const selectedNodeId = ref(null)
const nodesBusy = ref(false)
const sandboxes = ref([])
const deviceIds = ref([])

async function loadNodes(live = false) {
  nodesBusy.value = true
  try {
    const data = await api.post('/nodes/get_all_nodes', live ? { refresh: true } : {})
    nodes.value = data.nodes || []
    const cur = nodes.value.find(n => n.node_id === selectedNodeId.value)
    if (!cur) {
      const first = nodes.value.find(n => n.status === 'online')
      selectedNodeId.value = first ? first.node_id : null
    }
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    nodesBusy.value = false
  }
}

const selectedNode = computed(() =>
  nodes.value.find(n => n.node_id === selectedNodeId.value) || null)
const nodeOnline = computed(() => selectedNode.value?.status === 'online')

function selectNode(nodeId) {
  selectedNodeId.value = nodeId
  deviceIds.value = []
  loadSandboxes()
  loadQueue(true)
}

async function loadSandboxes() {
  if (!selectedNodeId.value) { sandboxes.value = []; return }
  try {
    const data = await api.get(`/nodes/${selectedNodeId.value}/sandboxes`)
    sandboxes.value = data.sandboxes || []
  } catch {
    sandboxes.value = []
  }
}

// ── 提交表单 ────────────────────────────────────────────────
const form = reactive({
  cpu: 0,
  memory: 0,
  memUnit: 'GB',
  deviceNum: 0,
  priority: 0,
  estTime: '',
  command: '',
  targetType: 'host',
  container: '',
  containerWorkdir: '',
  containerUser: '',
  containerEnv: '',
})
const submitting = ref(false)
const submittingBatch = ref(false)

const memMax = computed(() => form.memUnit === 'GB' ? 256 : 65536)

function buildTarget() {
  if (form.targetType !== 'docker_existing') return { type: 'host' }
  const container = form.container.trim()
  if (!container) throw new Error('请输入已有 Docker 容器名称或 ID')
  const env = {}
  for (const rawLine of form.containerEnv.split('\n')) {
    const line = rawLine.trim()
    if (!line) continue
    const i = line.indexOf('=')
    if (i <= 0) throw new Error(`环境变量格式错误: ${line}`)
    env[line.slice(0, i).trim()] = line.slice(i + 1)
  }
  return {
    type: 'docker_existing',
    container,
    workdir: form.containerWorkdir.trim(),
    user: form.containerUser.trim(),
    env,
  }
}

function buildBody(command) {
  return {
    node_id: selectedNodeId.value,
    command,
    cpu: form.cpu,
    memory: form.memory,
    mem_unit: form.memUnit,
    device_num: deviceIds.value.length > 0 ? 0 : form.deviceNum,
    device_ids: deviceIds.value.length > 0 ? deviceIds.value.map(String) : undefined,
    est_time: parseInt(form.estTime, 10) || 0,
    priority: form.priority,
    target: buildTarget(),
  }
}

async function submitCommand() {
  if (!nodeOnline.value) { toast('请先选择一个在线节点', 'error'); return }
  const command = joinCommandLines(form.command)
  if (!command) { toast('请输入命令', 'error'); return }
  let body
  try { body = buildBody(command) } catch (e) { toast(e.message, 'error'); return }

  submitting.value = true
  try {
    const data = await api.post('/tasks', body)
    toast(`任务已提交，队列位置 #${data.position}`, 'success')
    form.command = ''
    loadQueue(true)
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    submitting.value = false
  }
}

const commandLines = computed(() =>
  form.command.trim().split('\n').map(s => s.trim()).filter(Boolean))

async function submitBatch() {
  if (!nodeOnline.value) { toast('请先选择一个在线节点', 'error'); return }
  const lines = commandLines.value
  if (lines.length < 2) { toast('批量执行至少需要 2 行命令', 'error'); return }
  let target
  try { target = buildTarget() } catch (e) { toast(e.message, 'error'); return }
  if (!window.confirm(
    `将提交 ${lines.length} 个任务（资源统一为 CPU=${form.cpu}, ` +
    `内存=${form.memory}${form.memUnit}, 设备=${form.deviceNum}）\n\n是否继续？`)) return

  submittingBatch.value = true
  let ok = 0, fail = 0
  for (let i = 0; i < lines.length; i++) {
    try {
      await api.post('/tasks', { ...buildBody(lines[i]), target })
      ok++
    } catch { fail++ }
  }
  submittingBatch.value = false
  toast(`批量提交完成: 成功 ${ok} 个, 失败 ${fail} 个`, fail ? 'error' : 'success')
  form.command = ''
  loadQueue(true)
}

// ── 队列 ────────────────────────────────────────────────────
const queueMode = ref('mine')   // mine | all
const queue = ref([])           // 展平后的任务行
const queueLoading = ref(false)
const showNodeCol = ref(false)
const checked = ref([])

async function loadQueue(force = false) {
  if (queueMode.value === 'all' && !selectedNodeId.value) {
    queue.value = []
    return
  }
  queueLoading.value = true
  try {
    if (queueMode.value === 'mine' && !selectedNodeId.value) {
      // 跨节点聚合
      const data = await api.get('/tasks/mine')
      showNodeCol.value = true
      queue.value = (data.groups || []).flatMap(g =>
        g.tasks.map(t => ({ ...t, node_name: g.node_name, node_id: g.node_id })))
    } else if (queueMode.value === 'mine') {
      const data = await api.get(`/tasks/mine?node_id=${encodeURIComponent(selectedNodeId.value)}`)
      showNodeCol.value = false
      queue.value = data.queue || []
    } else {
      const data = await api.get(`/tasks?node_id=${encodeURIComponent(selectedNodeId.value)}`)
      showNodeCol.value = false
      queue.value = data.queue || []
    }
    checked.value = []
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    queueLoading.value = false
  }
}

watch(queueMode, () => loadQueue(true))
watch(selectedNodeId, () => loadQueue(true))

// ── 标注（localStorage） ────────────────────────────────────
const markKey = computed(() => `neu_marks_${user.value?.username || 'anon'}`)
const marked = ref(loadMarks())
function loadMarks() {
  try { return new Set(JSON.parse(localStorage.getItem(markKey.value) || '[]')) } catch { return new Set() }
}
function saveMarks() {
  try { localStorage.setItem(markKey.value, JSON.stringify([...marked.value])) } catch { /* 忽略 */ }
}
function toggleMark(taskId) {
  if (marked.value.has(taskId)) marked.value.delete(taskId)
  else marked.value.add(taskId)
  saveMarks()
}

// ── 删除 ────────────────────────────────────────────────────
async function batchDelete() {
  if (!checked.value.length) return
  if (!window.confirm(`确定删除 ${checked.value.length} 个任务吗？（运行中的任务将被强制终止）`)) return
  try {
    const data = await api.delete('/tasks', {
      node_id: selectedNodeId.value,
      task_ids: checked.value,
    })
    if (data && data.denied?.length) {
      toast(`已删除部分任务；被拒 ${data.denied.length} 个（非本人任务）`, 'error')
    } else {
      toast(data?.message || '已删除', 'success')
    }
    loadQueue(true)
  } catch (e) {
    toast(e.message, 'error')
  }
}

function toggleCheck(taskId) {
  const set = new Set(checked.value)
  if (set.has(taskId)) set.delete(taskId)
  else set.add(taskId)
  checked.value = [...set]
}

// ── 重跑 ────────────────────────────────────────────────────
const rerunOpen = ref(false)
const rerun = reactive({ task: null, command: '', cpu: 0, memory: 0, deviceNum: 0 })
function openRerun(task) {
  rerun.task = task
  rerun.command = task.command || ''
  rerun.cpu = task.cpu || 0
  rerun.memory = memToGB(task.mem)
  rerun.deviceNum = task.device_num || 0
  rerunOpen.value = true
}
async function confirmRerun() {
  if (!rerun.command.trim()) { toast('命令不能为空', 'error'); return }
  try {
    const data = await api.post('/tasks', {
      node_id: rerun.task.node_id || selectedNodeId.value,
      command: rerun.command.trim(),
      cpu: rerun.cpu,
      memory: rerun.memory,
      mem_unit: 'GB',
      device_num: rerun.deviceNum,
      priority: rerun.task.priority || 0,
      target: { type: 'host' },
    })
    toast(`已重新提交，队列位置 #${data.position}`, 'success')
    rerunOpen.value = false
    loadQueue(true)
  } catch (e) {
    toast(e.message, 'error')
  }
}

// ── 日志 ────────────────────────────────────────────────────
const logTask = ref(null)
const logNodeId = ref('')
function viewTaskLog(task) {
  logTask.value = { ...task }
  logNodeId.value = task.node_id || selectedNodeId.value || ''
}

// ── 保存为实验 ──────────────────────────────────────────────
const expOpen = ref(false)
const exp = reactive({ task: null, title: '', tags: '', folderId: '' })
const folders = ref([])
async function loadFolders() {
  try {
    const data = await api.get('/experiments/folders')
    folders.value = data.folders || []
  } catch { folders.value = [] }
}
function flattenFolders(items, depth = 0, out = []) {
  for (const f of items || []) {
    out.push({ id: f.id, label: `${'　'.repeat(depth)}${f.name}` })
    flattenFolders(f.children, depth + 1, out)
  }
  return out
}
const folderOptions = computed(() => flattenFolders(folders.value))

function openSaveExp(task) {
  exp.task = task
  exp.title = (task.command || '').substring(0, 80)
  exp.tags = ''
  exp.folderId = ''
  loadFolders()
  expOpen.value = true
}
async function confirmSaveExp() {
  const title = exp.title.trim()
  if (!title) { toast('请输入实验标题', 'error'); return }
  const tags = exp.tags.split(/[,，]/).map(s => s.trim()).filter(Boolean)
  const task = exp.task
  const blocks = [{
    type: 'task',
    command: task.command || '',
    task_id: task.task_id,
    node_id: logNodeId.value || task.node_id || '',
  }]
  const logs = {}
  try {
    const r = await fetch(
      `/tasks/${task.task_id}/log?node_id=${encodeURIComponent(logNodeId.value)}&raw=1`)
    if (r.ok) logs[task.task_id] = await r.text()
  } catch { /* 日志获取失败不阻断保存 */ }
  try {
    await api.post('/experiments/', {
      title, blocks, tags, logs,
      folder_id: exp.folderId || null,
    })
    toast('实验记录已创建', 'success')
    expOpen.value = false
  } catch (e) {
    toast(e.message, 'error')
  }
}

// ── 节点管理入口 ────────────────────────────────────────────
function manageNodes() {
  if (user.value?.role === 'admin') window.location.href = '/admin/nodes'
  else toast('仅管理员可管理节点', 'error')
}

// ── 轮询 ────────────────────────────────────────────────────
// 后台轮询用节点状态缓存（不产生 worker I/O），手动「刷新」才实时查询；
// 标签页隐藏时自动暂停（setPolling），避免多标签页请求堆积拖垮 master
let nodesTimer = null
let queueTimer = null
onMounted(() => {
  loadNodes(true)
  loadSandboxes()
  loadQueue()
  nodesTimer = setPolling(() => loadNodes(false), 60000)
  queueTimer = setPolling(() => loadQueue(), 8000)
})
onBeforeUnmount(() => {
  if (nodesTimer) nodesTimer()
  if (queueTimer) queueTimer()
})
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h1 class="page-title">任务</h1>
        <div class="page-desc">提交命令任务 · 管理自己的队列 · 查看主队列</div>
      </div>
    </div>

    <div class="tasks-layout" :class="{ 'with-log': !!logTask }">
      <!-- 左栏：节点 -->
      <div class="card">
        <div class="card-body">
          <NodeList
            :nodes="nodes"
            :selected-id="selectedNodeId"
            :busy="nodesBusy"
            :device-ids="deviceIds"
            :sandboxes="sandboxes"
            @select="selectNode"
            @refresh="() => loadNodes(true)"
            @manage="manageNodes"
            @update:device-ids="v => deviceIds = v"
          />
        </div>
      </div>

      <!-- 中栏：表单 + 队列 -->
      <div class="col-mid">
        <div class="card">
          <div class="card-head">
            <Icon name="terminal" :size="14" />
            <span class="card-title">提交任务</span>
            <span class="grow" />
            <span v-if="selectedNode" class="card-sub">
              → {{ selectedNode.name }}
            </span>
          </div>
          <div class="card-body">
            <div class="input-row">
              <div class="field" style="flex:1">
                <label class="field-label">CPU <span class="field-hint">核心数，0 不限制</span></label>
                <Stepper v-model="form.cpu" :min="0" :max="256" />
              </div>
              <div class="field" style="flex:1">
                <label class="field-label">内存 <span class="field-hint">0 不限制</span></label>
                <div class="row">
                  <Stepper v-model="form.memory" :min="0" :max="memMax" />
                  <div class="tabs" style="transform:scale(.92)">
                    <button class="tab" :class="{ active: form.memUnit === 'GB' }"
                            @click="form.memUnit = 'GB'">GB</button>
                    <button class="tab" :class="{ active: form.memUnit === 'MB' }"
                            @click="form.memUnit = 'MB'">MB</button>
                  </div>
                </div>
              </div>
              <div class="field" style="flex:1">
                <label class="field-label">设备 <span class="field-hint">卡数</span></label>
                <Stepper v-model="form.deviceNum" :min="0" :max="16" />
              </div>
            </div>

            <div class="field">
              <label class="field-label">命令 <span class="field-hint">每行一条，自动 && 拼接，\ 续行</span></label>
              <textarea v-model="form.command" class="textarea mono" rows="4"
                        placeholder="nvidia-smi&#10;python train.py" />
            </div>

            <div class="input-row">
              <div class="field grow">
                <label class="field-label">执行目标</label>
                <select v-model="form.targetType" class="select">
                  <option value="host">Host</option>
                  <option value="docker_existing">已有 Docker 容器</option>
                </select>
              </div>
              <div class="field" style="width:130px">
                <label class="field-label">优先级</label>
                <select v-model.number="form.priority" class="select">
                  <option :value="0">普通</option>
                  <option :value="1">赶论文</option>
                </select>
              </div>
              <div class="field" style="width:110px">
                <label class="field-label">预估耗时 <span class="field-hint">min</span></label>
                <input v-model="form.estTime" type="number" min="0" class="input"
                       placeholder="选填">
              </div>
            </div>

            <div v-if="form.targetType === 'docker_existing'" class="input-row">
              <div class="field grow">
                <label class="field-label">容器 <span class="field-hint">名称或 ID，必填</span></label>
                <input v-model="form.container" class="input" placeholder="training-01">
              </div>
              <div class="field grow">
                <label class="field-label">容器工作目录 <span class="field-hint">可选</span></label>
                <input v-model="form.containerWorkdir" class="input" placeholder="/workspace">
              </div>
              <div class="field grow">
                <label class="field-label">容器用户 <span class="field-hint">可选</span></label>
                <input v-model="form.containerUser" class="input" placeholder="root 或 1000:1000">
              </div>
            </div>
            <div v-if="form.targetType === 'docker_existing'" class="field">
              <label class="field-label">环境变量 <span class="field-hint">每行 KEY=VALUE</span></label>
              <textarea v-model="form.containerEnv" class="textarea mono" rows="2"
                        placeholder="MODEL=resnet50" />
            </div>

            <div class="row mt-8">
              <button class="btn btn-primary" :disabled="!nodeOnline || submitting || submittingBatch"
                      @click="submitCommand">
                <Icon name="play" :size="14" />
                {{ submitting ? '提交中…' : '提交命令' }}
              </button>
              <button class="btn" :disabled="!nodeOnline || submitting || submittingBatch
                                             || commandLines.length < 2"
                      @click="submitBatch">
                批量执行（{{ commandLines.length }} 行 = {{ commandLines.length }} 个任务）
              </button>
              <span v-if="!nodeOnline" class="text-3 small">选择在线节点后可提交</span>
            </div>
          </div>
        </div>

        <!-- 队列 -->
        <div class="card mt-16">
          <div class="card-head">
            <div class="tabs">
              <button class="tab" :class="{ active: queueMode === 'mine' }"
                      @click="queueMode = 'mine'">
                我的队列
                <span v-if="!selectedNodeId" class="count">全部节点</span>
              </button>
              <button class="tab" :class="{ active: queueMode === 'all' }"
                      @click="queueMode = 'all'">
                全部队列
                <span v-if="selectedNode" class="count">{{ selectedNode.name }}</span>
              </button>
            </div>
            <span class="grow" />
            <span v-if="queueMode === 'all' && !selectedNodeId" class="card-sub">
              请选择节点
            </span>
            <button class="btn btn-ghost btn-icon" title="刷新队列"
                    :class="{ spin: queueLoading }" @click="loadQueue(true)">
              <Icon name="refresh" :size="14" />
            </button>
          </div>

          <div v-if="checked.length && !(queueMode === 'mine' && !selectedNodeId)" class="row"
               style="padding:8px 14px;border-bottom:1px solid var(--border);background:var(--surface-2)">
            <span class="small">已选 {{ checked.length }} 项</span>
            <span class="grow" />
            <button class="btn btn-ghost btn-sm" @click="checked = []">取消</button>
            <button class="btn btn-danger btn-sm" @click="batchDelete">
              <Icon name="trash" :size="13" /> 删除选中
            </button>
          </div>

          <div class="card-body flush">
            <QueueList
              :tasks="queue"
              :my-username="user?.username"
              :checked="checked"
              :marked="[...marked]"
              :show-node="showNodeCol"
              @select-task="viewTaskLog"
              @toggle-check="toggleCheck"
              @toggle-mark="toggleMark"
              @rerun="openRerun"
            />
            <div v-if="queueMode === 'all' && !selectedNodeId" class="empty-state">
              <div class="icon">🖥</div>
              <p>在左侧选择一个节点查看该节点的主队列</p>
            </div>
          </div>
        </div>
      </div>

      <!-- 右栏：日志 -->
      <div v-if="logTask" class="col-log">
        <TaskLog
          :task="logTask"
          :node-id="logNodeId"
          @close="logTask = null"
          @save-exp="openSaveExp"
        />
      </div>
    </div>

    <!-- 重跑弹窗 -->
    <Modal v-model="rerunOpen" title="重新执行命令">
      <div class="field">
        <label class="field-label">命令</label>
        <textarea v-model="rerun.command" class="textarea mono" rows="8" />
      </div>
      <div class="input-row">
        <div class="field">
          <label class="field-label">CPU</label>
          <input v-model.number="rerun.cpu" type="number" min="0" class="input">
        </div>
        <div class="field">
          <label class="field-label">内存 (GB)</label>
          <input v-model.number="rerun.memory" type="number" min="0" class="input">
        </div>
        <div class="field">
          <label class="field-label">设备数</label>
          <input v-model.number="rerun.deviceNum" type="number" min="0" class="input">
        </div>
      </div>
      <template #footer>
        <button class="btn" @click="rerunOpen = false">取消</button>
        <button class="btn btn-primary" @click="confirmRerun">确认执行</button>
      </template>
    </Modal>

    <!-- 保存为实验弹窗 -->
    <Modal v-model="expOpen" title="保存为实验记录">
      <div class="field">
        <label class="field-label">实验标题</label>
        <input v-model="exp.title" class="input" placeholder="输入实验标题">
      </div>
      <div class="field">
        <label class="field-label">标签 <span class="field-hint">逗号分隔</span></label>
        <input v-model="exp.tags" class="input" placeholder="breakdown, resnet50">
      </div>
      <div class="field">
        <label class="field-label">文件夹</label>
        <select v-model="exp.folderId" class="select">
          <option value="">— 无 —</option>
          <option v-for="f in folderOptions" :key="f.id" :value="f.id">{{ f.label }}</option>
        </select>
      </div>
      <template #footer>
        <button class="btn" @click="expOpen = false">取消</button>
        <button class="btn btn-primary" @click="confirmSaveExp">保存</button>
      </template>
    </Modal>
  </div>
</template>

<style scoped>
.tasks-layout {
  display: grid;
  grid-template-columns: 330px minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}
.tasks-layout.with-log {
  grid-template-columns: 330px minmax(0, 1fr) 460px;
}
.col-log {
  position: sticky;
  top: 24px;
  height: calc(100vh - 48px);
  min-height: 480px;
}
.col-mid { min-width: 0; display: flex; flex-direction: column; }
@media (max-width: 1280px) {
  .tasks-layout, .tasks-layout.with-log { grid-template-columns: 300px minmax(0, 1fr); }
  .col-log { grid-column: 1 / -1; position: static; height: 640px; }
}
</style>
