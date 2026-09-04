<script setup>
/**
 * 实验列表：文件夹树 + 搜索 + 我的/全部 过滤。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import Icon from '../components/Icon.vue'
import { api } from '../api'
import { authRO, toast } from '../store'
import { formatTimeShort } from '../utils'

const user = computed(() => authRO.user)

const experiments = ref([])
const folders = ref([])
const search = ref('')
const scope = ref('all')      // all | mine
const folderId = ref('')
const expanded = ref(new Set())
const loading = ref(false)

const canModifyAll = computed(() => user.value?.role === 'admin')

async function loadFolders() {
  try {
    const data = await api.get('/experiments/folders')
    folders.value = data.folders || []
  } catch { /* 忽略 */ }
}

async function loadExperiments() {
  loading.value = true
  try {
    const params = new URLSearchParams()
    if (search.value.trim()) params.set('search', search.value.trim())
    if (folderId.value) params.set('folder_id', folderId.value)
    if (scope.value === 'mine') params.set('scope', 'mine')
    const data = await api.get(`/experiments/?${params}`)
    experiments.value = data.experiments || []
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    loading.value = false
  }
}

function selectFolder(id) {
  folderId.value = id
  loadExperiments()
}

function toggleExpand(id) {
  if (expanded.value.has(id)) expanded.value.delete(id)
  else expanded.value.add(id)
  expanded.value = new Set(expanded.value)
}

async function createFolder(parentId) {
  const name = window.prompt('新文件夹名称：')
  if (!name || !name.trim()) return
  try {
    await api.post('/experiments/folders', { name: name.trim(), parent_id: parentId || null })
    if (parentId) {
      expanded.value.add(parentId)
      expanded.value = new Set(expanded.value)
    }
    await loadFolders()
    await loadExperiments()
  } catch (e) {
    toast(e.message, 'error')
  }
}

async function renameFolder(folder) {
  const name = window.prompt('重命名文件夹：', folder.name)
  if (!name || !name.trim() || name.trim() === folder.name) return
  try {
    await api.put(`/experiments/folders/${folder.id}`, { name: name.trim() })
    await loadFolders()
  } catch (e) {
    toast(e.message, 'error')
  }
}

async function deleteFolder(folder) {
  if (!window.confirm(`确定删除文件夹「${folder.name}」？子文件夹会上移，实验会移到上级文件夹。`)) return
  try {
    await api.delete(`/experiments/folders/${folder.id}`)
    if (folderId.value === folder.id) folderId.value = ''
    await loadFolders()
    await loadExperiments()
  } catch (e) {
    toast(e.message, 'error')
  }
}

function blockSummary(exp) {
  const blocks = exp.blocks || []
  const parts = []
  const text = blocks.filter(b => b.type === 'text').length
  const task = blocks.filter(b => b.type === 'task').length
  if (text) parts.push(`${text} 笔记`)
  if (task) parts.push(`${task} 任务`)
  return parts.join(' · ') || '空'
}

let searchTimer = null
function onSearch() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(loadExperiments, 300)
}

onMounted(() => {
  loadFolders()
  loadExperiments()
})
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h1 class="page-title">实验</h1>
        <div class="page-desc">块式实验笔记：markdown 笔记 + 任务引用与日志</div>
      </div>
      <div class="page-actions">
        <router-link class="btn btn-primary" to="/experiments/new">
          <Icon name="plus" :size="14" /> 新建实验
        </router-link>
      </div>
    </div>

    <div class="exp-layout">
      <!-- 文件夹树 -->
      <div class="card">
        <div class="card-head">
          <span class="card-title">文件夹</span>
          <span class="grow" />
          <button class="btn btn-ghost btn-icon" title="新建根文件夹"
                  @click="createFolder(null)">
            <Icon name="plus" :size="14" />
          </button>
        </div>
        <div class="folder-tree">
          <div class="folder-item" :class="{ active: folderId === '' }"
               @click="selectFolder('')">
            <span class="arrow" style="visibility:hidden">▶</span>
            <Icon name="folder" :size="14" />
            <span class="f-name">全部实验</span>
          </div>

          <template v-for="f in folders" :key="f.id">
            <div class="folder-item" :class="{ active: folderId === f.id }"
                 @click="selectFolder(f.id)">
              <span v-if="f.children?.length" class="arrow"
                    :class="{ expanded: expanded.has(f.id) }"
                    @click.stop="toggleExpand(f.id)">▶</span>
              <span v-else class="arrow" style="visibility:hidden">▶</span>
              <Icon name="folder" :size="14" />
              <span class="f-name" :title="f.name">{{ f.name }}</span>
              <span class="f-count">{{ f.total_exp_count }}</span>
              <span class="f-actions">
                <button class="btn btn-ghost btn-icon" style="padding:2px"
                        title="新建子文件夹" @click.stop="createFolder(f.id)">
                  <Icon name="plus" :size="12" />
                </button>
                <button class="btn btn-ghost btn-icon" style="padding:2px"
                        title="重命名" @click.stop="renameFolder(f)">
                  <Icon name="edit" :size="12" />
                </button>
                <button class="btn btn-ghost btn-icon" style="padding:2px"
                        title="删除" @click.stop="deleteFolder(f)">
                  <Icon name="trash" :size="12" />
                </button>
              </span>
            </div>
            <div v-if="f.children?.length && expanded.has(f.id)" class="folder-children">
              <template v-for="c in f.children" :key="c.id">
                <div class="folder-item" :class="{ active: folderId === c.id }"
                     @click="selectFolder(c.id)">
                  <span v-if="c.children?.length" class="arrow"
                        :class="{ expanded: expanded.has(c.id) }"
                        @click.stop="toggleExpand(c.id)">▶</span>
                  <span v-else class="arrow" style="visibility:hidden">▶</span>
                  <Icon name="folder" :size="14" />
                  <span class="f-name" :title="c.name">{{ c.name }}</span>
                  <span class="f-count">{{ c.total_exp_count }}</span>
                  <span class="f-actions">
                    <button class="btn btn-ghost btn-icon" style="padding:2px"
                            title="新建子文件夹" @click.stop="createFolder(c.id)">
                      <Icon name="plus" :size="12" />
                    </button>
                    <button class="btn btn-ghost btn-icon" style="padding:2px"
                            title="重命名" @click.stop="renameFolder(c)">
                      <Icon name="edit" :size="12" />
                    </button>
                    <button class="btn btn-ghost btn-icon" style="padding:2px"
                            title="删除" @click.stop="deleteFolder(c)">
                      <Icon name="trash" :size="12" />
                    </button>
                  </span>
                </div>
              </template>
            </div>
          </template>
        </div>
      </div>

      <!-- 列表 -->
      <div class="card list-col">
        <div class="card-head">
          <div class="row grow" style="gap:6px">
            <Icon name="search" :size="14" class="text-3" />
            <input v-model="search" class="input" style="padding:5px 10px;font-size:12.5px"
                   placeholder="搜索标题 / 标签 / 内容…" @input="onSearch">
          </div>
          <div class="tabs">
            <button class="tab" :class="{ active: scope === 'all' }"
                    @click="scope = 'all'; loadExperiments()">全部</button>
            <button class="tab" :class="{ active: scope === 'mine' }"
                    @click="scope = 'mine'; loadExperiments()">我的</button>
          </div>
          <button class="btn btn-ghost btn-icon" title="刷新"
                  :class="{ spin: loading }" @click="loadExperiments()">
            <Icon name="refresh" :size="14" />
          </button>
        </div>
        <div class="card-body flush">
          <div v-if="!experiments.length" class="empty-state">
            <div class="icon">🧪</div>
            <p>暂无实验记录</p>
          </div>
          <router-link
            v-for="exp in experiments"
            :key="exp.id"
            class="exp-card"
            :to="{ name: 'experiment-detail', params: { id: exp.id } }"
          >
            <div class="e-title">{{ exp.title }}</div>
            <div class="e-meta">
              <span>{{ exp.created_by || '—' }}</span>
              <span>{{ blockSummary(exp) }}</span>
              <span>{{ formatTimeShort(exp.updated_at || exp.created_at) }}</span>
              <span v-for="t in (exp.tags || []).slice(0, 4)" :key="t" class="tag">{{ t }}</span>
            </div>
          </router-link>
        </div>
      </div>
    </div>
  </div>
</template>
