<template>
  <section class="page" data-module="vessel">
    <header class="page-head">
      <div>
        <h2>压力容器管理</h2>
        <p class="page-desc">按容器状态分组查看合规判定，设计压力或容积规格超出允许范围的标记并置顶；范围可下发调整，历史判定按当时范围保留。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记压力容器</button>
        <button class="btn" type="button" @click="exportRows">导出压力容器清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">容器总数</span>
        <strong class="stat-value">{{ visibleTotal }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">超范围台数</span>
        <strong class="stat-value stat-danger">{{ view.overRangeGlobal }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待补全</span>
        <strong class="stat-value stat-warn">{{ view.pendingGlobal }}</strong>
      </article>
    </div>

    <section class="range-panel">
      <header class="range-head">
        <div>
          <strong>允许范围（当前 {{ current.version }}）</strong>
          <span class="range-current">
            设计压力 {{ fmt(current.pressure_min) }}~{{ fmt(current.pressure_max) }} MPa ／
            容积规格 {{ fmt(current.volume_min) }}~{{ fmt(current.volume_max) }} m³
          </span>
        </div>
        <button class="btn ghost" type="button" @click="showRangeForm = !showRangeForm">
          {{ showRangeForm ? '收起范围下发' : '下发新范围' }}
        </button>
      </header>
      <form v-if="showRangeForm" class="range-form" @submit.prevent="submitRange">
        <label v-for="field in rangeFields" :key="field.key" class="filter-item">
          <span>{{ field.label }}</span>
          <input v-model.number="rangeForm[field.key]" type="number" step="0.01" min="0" required />
        </label>
        <button class="btn primary" type="submit" :disabled="adjusting">
          {{ adjusting ? '下发中…' : '按新范围重判' }}
        </button>
        <span v-if="rangeMessage" :class="rangeOk ? 'ok-text' : 'error-text'">{{ rangeMessage }}</span>
        <p class="range-tip">同一次调整重复下发只生效一次；调整后全部容器按新范围重判，历史判定仍保留在版本史里。</p>
      </form>
      <ul class="range-versions">
        <li v-for="item in view.ranges" :key="item.version">
          <strong>{{ item.version }}</strong>
          <span>{{ item.source }}</span>
          <span>生效 {{ item.effective_at }}</span>
          <span>压力 {{ fmt(item.pressure_min) }}~{{ fmt(item.pressure_max) }}</span>
          <span>容积 {{ fmt(item.volume_min) }}~{{ fmt(item.volume_max) }}</span>
        </li>
      </ul>
    </section>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>容器编号</span>
        <input v-model="keyword" placeholder="按容器编号检索" />
      </label>
      <label class="filter-item">
        <span>容器状态</span>
        <select v-model="statusFilter">
          <option value="">全部状态</option>
          <option v-for="name in statuses" :key="name" :value="name">{{ name }}</option>
          <option value="待补全">待补全</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <template v-for="group in visibleGroups" :key="group.status">
      <h3 class="group-title" :class="{ 'group-pending': group.status === '待补全' }">
        {{ group.status }}
        <span class="group-count">{{ group.items.length }} 台</span>
        <span v-if="group.status === '待补全'" class="tag tag-pending">规格缺失</span>
      </h3>
      <table class="data-table group-table">
        <thead>
          <tr>
            <th v-for="column in columns" :key="column">{{ column }}</th>
            <th>合规视图</th>
            <th>合规判定操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in group.items"
            :key="String(row.id)"
            :class="{ 'row-over': row.compliance.result === '超范围', 'row-pending-cell': row.compliance.result === '待补全' }"
          >
            <td v-for="column in columns" :key="column">{{ row[column] || '—' }}</td>
            <td>
              <div class="compliance-cell">
                <span class="tag" :class="tagClass(row.compliance.result)">{{ row.compliance.result }}</span>
                <span class="compliance-reason">{{ row.compliance.reason }}</span>
                <span class="compliance-meta">判定范围 {{ row.compliance.version ?? '—' }} · {{ row.compliance.source }}</span>
              </div>
            </td>
            <td class="row-actions">
              <template v-if="row.compliance.locked">
                <span class="muted-text">已停用/维护封闭，仅可查看</span>
                <button class="link" type="button" @click.stop="openDetail(row)">查看详情</button>
              </template>
              <template v-else>
                <button
                  v-for="action in availableActions(row.status)"
                  :key="action"
                  class="link"
                  type="button"
                  @click.stop="runAction(action, row)"
                >
                  {{ action }}
                </button>
                <button class="link" type="button" @click.stop="openDetail(row)">判定史</button>
              </template>
            </td>
          </tr>
        </tbody>
      </table>
    </template>

    <p v-if="!visibleGroups.length" class="empty-state block-empty">当前过滤条件下没有压力容器。</p>

    <footer class="page-foot">
      <span>
        当前视图 {{ visibleTotal }} 台，超范围 {{ visibleOverRange }} 台
        <template v-if="isFiltered">（全局 {{ view.overRangeGlobal }} 台，与运营概览一致）</template>
      </span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { request } from '@/api/client'

type Compliance = {
  result: '合规' | '超范围' | '待补全'
  reason: string
  version: string | null
  source: string
  judgedAt?: string
  locked: boolean
}
type VesselRow = Record<string, string | number | null> & { id: number; status: string; compliance: Compliance }
type Group = { status: string; items: VesselRow[] }
type RangeVersion = {
  version: string
  source: string
  effective_at: string
  pressure_min: number
  pressure_max: number
  volume_min: number
  volume_max: number
}
type ComplianceView = {
  groups: Group[]
  total: number
  overRange: number
  overRangeGlobal: number
  pendingGlobal: number
  currentRange: RangeVersion
  ranges: RangeVersion[]
}

const ENDPOINT = '/api/vessel'
const columns = ['容器编号', '容器名称', '设计压力', '容积规格', '介质类别', '使用场所', '下次检验日', '容器状态']
const statuses = ['待投用', '在用运行', '停用待检', '已报废']
const actionByStatus: Record<string, string[]> = {
  待投用: ['办理投用'],
  在用运行: ['安排检验'],
  停用待检: ['报废容器'],
}

const router = useRouter()
const emptyView: ComplianceView = {
  groups: [],
  total: 0,
  overRange: 0,
  overRangeGlobal: 0,
  pendingGlobal: 0,
  currentRange: {
    version: 'v0', source: '', effective_at: '',
    pressure_min: 0, pressure_max: 0, volume_min: 0, volume_max: 0,
  },
  ranges: [],
}

const view = ref<ComplianceView>(emptyView)
const keyword = ref('')
const statusFilter = ref('')
const errorMessage = ref('')
const showRangeForm = ref(false)
const adjusting = ref(false)
const rangeMessage = ref('')
const rangeOk = ref(false)
const rangeFields = [
  { key: 'pressure_min', label: '设计压力下限 MPa' },
  { key: 'pressure_max', label: '设计压力上限 MPa' },
  { key: 'volume_min', label: '容积下限 m³' },
  { key: 'volume_max', label: '容积上限 m³' },
] as const
const rangeForm = reactive<Record<string, number>>({
  pressure_min: 0, pressure_max: 0, volume_min: 0, volume_max: 0,
})

const current = computed(() => view.value.currentRange)
const isFiltered = computed(() => Boolean(keyword.value || statusFilter.value))
// 过滤待补全状态时，只保留待补全分组；其余状态过滤照常。
const visibleGroups = computed(() => {
  if (statusFilter.value === '待补全') {
    return view.value.groups.filter((group) => group.status === '待补全')
  }
  return view.value.groups
})
const visibleTotal = computed(() => visibleGroups.value.reduce((sum, group) => sum + group.items.length, 0))
const visibleOverRange = computed(
  () => visibleGroups.value.reduce(
    (sum, group) => sum + group.items.filter((item) => item.compliance.result === '超范围').length,
    0,
  ),
)

function fmt(value: number): string {
  return Number(value).toString()
}

function tagClass(result: Compliance['result']): string {
  if (result === '超范围') return 'tag-over'
  if (result === '待补全') return 'tag-pending'
  return 'tag-ok'
}

function availableActions(status: string): string[] {
  return actionByStatus[status] ?? []
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '压力容器登记入口尚未接入审批流'
}

function openDetail(row: VesselRow) {
  void router.push(`/vessel/${row.id}`)
}

async function runAction(action: string, row: VesselRow) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/compliance-actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || '压力容器动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '压力容器操作失败'
  }
}

async function submitRange() {
  rangeMessage.value = ''
  adjusting.value = true
  // 每次下发生成客户端令牌：同一次调整重发（网络重试、重复点击）只生效一次。
  const token = `adj-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  try {
    const response = await request(`${ENDPOINT}/ranges/adjust`, {
      method: 'POST',
      body: JSON.stringify({ values: { ...rangeForm, token } }),
    })
    const payload = await response.json()
    rangeOk.value = Boolean(payload.ok)
    rangeMessage.value = payload.message || '范围下发失败'
    if (payload.ok) {
      showRangeForm.value = false
      await reload()
    }
  } catch (error) {
    rangeOk.value = false
    rangeMessage.value = error instanceof Error ? error.message : '范围下发失败'
  } finally {
    adjusting.value = false
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (keyword.value) query.set('keyword', keyword.value)
  if (statusFilter.value && statusFilter.value !== '待补全') query.set('status', statusFilter.value)
  try {
    const response = await request(`${ENDPOINT}/compliance?${query.toString()}`)
    if (!response.ok) {
      throw new Error('合规视图读取失败')
    }
    const payload = (await response.json()) as ComplianceView
    view.value = payload
    if (!rangeForm.pressure_min) {
      rangeForm.pressure_min = payload.currentRange.pressure_min
      rangeForm.pressure_max = payload.currentRange.pressure_max
      rangeForm.volume_min = payload.currentRange.volume_min
      rangeForm.volume_max = payload.currentRange.volume_max
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '合规视图读取失败'
  }
}

onMounted(reload)
</script>
