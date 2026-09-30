<template>
  <section class="page" data-module="vessel">
    <header class="page-head">
      <div>
        <h2>压力容器管理 · 合规视图</h2>
        <p class="page-desc">
          每台容器的设计压力、容积规格对照当前允许范围呈现；超范围自动标记并排组内最前，
          设计压力或容积规格缺失的归入待补全。停用、报废容器只允许查看。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记压力容器</button>
        <button class="btn" type="button" @click="openRangePanel">调整允许范围</button>
        <button class="btn" type="button" @click="exportRows">导出压力容器清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in statCards" :key="item.label" class="stat-card" :class="item.tone">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>容器编号</span>
        <input v-model="keyword" placeholder="按容器编号检索" />
      </label>
      <label class="filter-item">
        <span>容器状态</span>
        <select v-model="statusFilter">
          <option value="">全部状态</option>
          <option v-for="name in statusOrder" :key="name" :value="name">{{ name }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
      <span class="range-tip">
        当前允许范围（第 {{ range.revision }} 版）：设计压力
        {{ range.pressure_min }}～{{ range.pressure_max }} MPa，容积
        {{ range.volume_min }}～{{ range.volume_max }} m³
      </span>
    </form>

    <div v-for="group in groups" :key="group.group" class="group-block">
      <div class="group-head" @click="toggleGroup(group.group)">
        <span class="group-caret">{{ collapsed[group.group] ? '▶' : '▼' }}</span>
        <strong>{{ group.group }}</strong>
        <span class="group-count">{{ group.items.length }} 台</span>
        <span v-if="group.kind === 'incomplete'" class="badge incomplete">待补全</span>
      </div>

      <table v-show="!collapsed[group.group]" class="data-table">
        <thead>
          <tr>
            <th>容器编号</th>
            <th>容器名称</th>
            <th>合规视图</th>
            <th>设计压力（MPa）</th>
            <th>容积规格（m³）</th>
            <th>使用场所</th>
            <th>容器状态</th>
            <th>判定来源</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in group.items" :key="String(row.id)" :class="resultClass(row)">
            <td>{{ row['容器编号'] ?? '—' }}</td>
            <td>{{ row['容器名称'] ?? '—' }}</td>
            <td>
              <span class="badge" :class="resultClass(row)">
                {{ row.compliance.result }}
                <em v-if="row.compliance.archived">（已归档）</em>
              </span>
              <p v-for="(reason, index) in row.compliance.reasons" :key="index" class="reason-text">
                {{ reason }}
              </p>
            </td>
            <td>
              <span :class="{ 'spec-over': row.compliance.pressure.within === false }">
                {{ formatSpec(row.compliance.pressure) }}
              </span>
              <span class="spec-range">允许 {{ rangeLabel(row.compliance.pressure) }}</span>
            </td>
            <td>
              <span :class="{ 'spec-over': row.compliance.volume.within === false }">
                {{ formatSpec(row.compliance.volume) }}
              </span>
              <span class="spec-range">允许 {{ rangeLabel(row.compliance.volume) }}</span>
            </td>
            <td>{{ row['使用场所'] ?? '—' }}</td>
            <td>{{ row.status }}</td>
            <td>{{ row.compliance.source }}<span class="muted"> · v{{ row.compliance.range_revision }}</span></td>
            <td class="row-actions">
              <template v-if="!row.compliance.closed">
                <button
                  v-for="action in allowedActions(row.status)"
                  :key="action"
                  class="link"
                  type="button"
                  @click="runAction(action, row)"
                >{{ action }}</button>
                <button
                  v-if="row.compliance.result === '待补全'"
                  class="link"
                  type="button"
                  @click="openComplete(row)"
                >补全资料</button>
              </template>
              <span v-else class="muted">封闭态·只读</span>
              <button class="link" type="button" @click="openDetail(row)">查看详情</button>
            </td>
          </tr>
          <tr v-if="!group.items.length">
            <td colspan="9" class="empty-state">该栏暂无压力容器</td>
          </tr>
        </tbody>
      </table>
    </div>

    <footer class="page-foot">
      <span>共 {{ summary.total || 0 }} 台压力容器，超范围 {{ summary.out_of_range || 0 }} 台，
        待补全 {{ summary.incomplete || 0 }} 台</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 登记弹窗 -->
    <div v-if="createVisible" class="modal-mask" @click.self="createVisible = false">
      <div class="modal">
        <h3>登记压力容器</h3>
        <p class="muted">登记后按当前允许范围自动给出第一条合规判定。</p>
        <label v-for="field in createFields" :key="field.key" class="modal-field">
          <span>{{ field.label }}<i v-if="field.required">*</i></span>
          <input v-model="createForm[field.key]" :placeholder="field.placeholder" />
        </label>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="createVisible = false">取消</button>
          <button class="btn primary" type="button" @click="submitCreate">确认登记</button>
        </div>
      </div>
    </div>

    <!-- 补全资料弹窗 -->
    <div v-if="completeVisible" class="modal-mask" @click.self="completeVisible = false">
      <div class="modal">
        <h3>补全资料 · {{ completing?.['容器编号'] }}</h3>
        <p class="muted">补全后按当前允许范围重新判定；只改补录字段，不动其他台账数据。</p>
        <label class="modal-field">
          <span>设计压力（MPa）</span>
          <input v-model="completeForm['设计压力']" placeholder="如 1.6MPa" />
        </label>
        <label class="modal-field">
          <span>容积规格（m³）</span>
          <input v-model="completeForm['容积规格']" placeholder="如 5 m³" />
        </label>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="completeVisible = false">取消</button>
          <button class="btn primary" type="button" @click="submitComplete">提交补全</button>
        </div>
      </div>
    </div>

    <!-- 允许范围调整弹窗 -->
    <div v-if="rangeVisible" class="modal-mask" @click.self="rangeVisible = false">
      <div class="modal wide">
        <h3>调整允许范围</h3>
        <p class="muted">
          调整下发后，非封闭且规格齐全的容器按新版本重新判定；历史判定仍按当时范围保留。
          同一次调整（相同下发单号）重复提交只生效一次。
        </p>
        <div class="range-grid">
          <label class="modal-field"><span>设计压力下限（MPa）</span><input v-model.number="rangeForm.pressure_min" type="number" step="0.01" /></label>
          <label class="modal-field"><span>设计压力上限（MPa）</span><input v-model.number="rangeForm.pressure_max" type="number" step="0.01" /></label>
          <label class="modal-field"><span>容积下限（m³）</span><input v-model.number="rangeForm.volume_min" type="number" step="0.01" /></label>
          <label class="modal-field"><span>容积上限（m³）</span><input v-model.number="rangeForm.volume_max" type="number" step="0.01" /></label>
          <label class="modal-field"><span>本次调整下发单号（幂等键）</span><input v-model="rangeForm.adjust_key" placeholder="如 RANGE-20260930-01" /></label>
          <label class="modal-field"><span>备注</span><input v-model="rangeForm.remark" placeholder="选填" /></label>
        </div>
        <table class="data-table range-history">
          <thead><tr><th>版本</th><th>设计压力范围</th><th>容积范围</th><th>生效时间</th><th>来源/备注</th></tr></thead>
          <tbody>
            <tr v-for="item in rangeHistory" :key="item.revision">
              <td>v{{ item.revision }}</td>
              <td>{{ item.pressure_min }}～{{ item.pressure_max }}</td>
              <td>{{ item.volume_min }}～{{ item.volume_max }}</td>
              <td>{{ item.effective_at }}</td>
              <td>{{ item.source }}<template v-if="item.remark"> · {{ item.remark }}</template></td>
            </tr>
          </tbody>
        </table>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="rangeVisible = false">关闭</button>
          <button class="btn primary" type="button" @click="submitRange">下发调整</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { request } from '@/api/client'

type SpecView = {
  text: string | number | null
  value: number | null
  min: number
  max: number
  within: boolean | null
}
type Compliance = {
  record_id: number
  result: '合规' | '超范围' | '待补全'
  reasons: string[]
  source: string
  range_revision: number
  effective_at: string
  archived: boolean
  closed: boolean
  pressure: SpecView
  volume: SpecView
}
type Row = Record<string, string | number | null> & {
  id: number
  status: string
  compliance: Compliance
}
type Group = { group: string; kind: 'status' | 'incomplete'; items: Row[] }
type RangeVersion = {
  revision: number
  pressure_min: number
  pressure_max: number
  volume_min: number
  volume_max: number
  effective_at: string
  source: string
  remark?: string | null
  adjust_key?: string | null
}

const ENDPOINT = '/api/vessel'
const UI_STATE_KEY = 'vessel-compliance-ui-state'
const statusOrder = ['待投用', '在用运行', '停用待检', '已报废']
const router = useRouter()

const groups = ref<Group[]>([])
const summary = ref({ total: 0, compliant: 0, out_of_range: 0, incomplete: 0, closed: 0 })
const range = ref<RangeVersion>({
  revision: 1, pressure_min: 0.1, pressure_max: 10, volume_min: 0.01, volume_max: 100,
  effective_at: '', source: '', remark: '', adjust_key: null,
})
const rangeHistory = ref<RangeVersion[]>([])
const errorMessage = ref('')

// 筛选与折叠状态随会话保留：刷新或从详情返回后，标记（服务端派生）与排列、筛选都还在。
const keyword = ref('')
const statusFilter = ref('')
const collapsed = reactive<Record<string, boolean>>({})

const statCards = computed(() => [
  { label: '容器总数', value: summary.value.total, tone: '' },
  { label: '合规台数', value: summary.value.compliant, tone: 'ok' },
  { label: '超范围台数', value: summary.value.out_of_range, tone: 'danger' },
  { label: '待补全台数', value: summary.value.incomplete, tone: 'warn' },
  { label: '封闭只读台数', value: summary.value.closed, tone: '' },
])

function persistUiState() {
  sessionStorage.setItem(UI_STATE_KEY, JSON.stringify({
    keyword: keyword.value,
    statusFilter: statusFilter.value,
    collapsed: { ...collapsed },
  }))
}

function restoreUiState() {
  try {
    const raw = sessionStorage.getItem(UI_STATE_KEY)
    if (!raw) return
    const state = JSON.parse(raw) as { keyword?: string; statusFilter?: string; collapsed?: Record<string, boolean> }
    keyword.value = state.keyword ?? ''
    statusFilter.value = state.statusFilter ?? ''
    Object.assign(collapsed, state.collapsed ?? {})
  } catch {
    // 会话状态损坏时忽略，按默认展开
  }
}

async function reload() {
  errorMessage.value = ''
  persistUiState()
  const params = new URLSearchParams()
  if (keyword.value.trim()) params.set('keyword', keyword.value.trim())
  if (statusFilter.value) params.set('status', statusFilter.value)
  try {
    const response = await request(`${ENDPOINT}/compliance/view?${params.toString()}`)
    if (!response.ok) throw new Error('合规视图读取失败')
    const payload = await response.json()
    groups.value = payload.groups ?? []
    summary.value = payload.summary ?? summary.value
    range.value = payload.current_range ?? range.value
    rangeHistory.value = payload.range_history ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '合规视图读取失败'
  }
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  void reload()
}

function toggleGroup(name: string) {
  collapsed[name] = !collapsed[name]
  persistUiState()
}

function resultClass(row: Row) {
  return {
    'row-ok': row.compliance.result === '合规',
    'row-over': row.compliance.result === '超范围',
    'row-incomplete': row.compliance.result === '待补全',
  }
}

function formatSpec(spec: SpecView) {
  if (spec.value === null) return '待补全'
  return spec.text ?? String(spec.value)
}

function rangeLabel(spec: SpecView) {
  return `${spec.min}～${spec.max}`
}

function allowedActions(status: string) {
  // 封闭态（停用待检/已报废）不提供任何可变更状态的动作，杜绝回退在用。
  if (status === '待投用') return ['办理投用']
  if (status === '在用运行') return ['安排检验', '报废容器']
  return []
}
async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.message || '状态切换未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '状态切换失败'
  }
}

function openDetail(row: Row) {
  persistUiState()
  void router.push(`/vessel/${row.id}`)
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

// ---- 登记 ----
const createVisible = ref(false)
const createFields = [
  { key: '容器编号', label: '容器编号', required: true, placeholder: '如 VESS-0101' },
  { key: '容器名称', label: '容器名称', required: true, placeholder: '如 储气罐' },
  { key: '设计压力', label: '设计压力（MPa）', required: true, placeholder: '如 1.6MPa' },
  { key: '容积规格', label: '容积规格（m³）', required: false, placeholder: '如 5 m³，缺失将进待补全' },
  { key: '使用场所', label: '使用场所', required: false, placeholder: '选填' },
] as const
const createForm = reactive<Record<string, string>>({})

function openCreate() {
  for (const field of createFields) createForm[field.key] = ''
  createVisible.value = true
}

async function submitCreate() {
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: { ...createForm } }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.message || '登记未生效')
    }
    createVisible.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  }
}

// ---- 补全资料 ----
const completeVisible = ref(false)
const completing = ref<Row | null>(null)
const completeForm = reactive<Record<string, string>>({ '设计压力': '', '容积规格': '' })

function openComplete(row: Row) {
  completing.value = row
  completeForm['设计压力'] = String(row.compliance.pressure.text ?? '')
  completeForm['容积规格'] = String(row.compliance.volume.text ?? '')
  completeVisible.value = true
}

async function submitComplete() {
  if (!completing.value) return
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${completing.value.id}/specs`, {
      method: 'PUT',
      body: JSON.stringify({ values: { ...completeForm } }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.message || '补全未生效')
    }
    completeVisible.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '补全失败'
  }
}

// ---- 允许范围调整 ----
const rangeVisible = ref(false)
const rangeForm = reactive({
  pressure_min: 0, pressure_max: 0, volume_min: 0, volume_max: 0,
  adjust_key: '', remark: '',
})

function openRangePanel() {
  rangeForm.pressure_min = range.value.pressure_min
  rangeForm.pressure_max = range.value.pressure_max
  rangeForm.volume_min = range.value.volume_min
  rangeForm.volume_max = range.value.volume_max
  rangeForm.adjust_key = ''
  rangeForm.remark = ''
  rangeVisible.value = true
}

async function submitRange() {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/compliance/range`, {
      method: 'PUT',
      body: JSON.stringify({
        pressure_min: rangeForm.pressure_min,
        pressure_max: rangeForm.pressure_max,
        volume_min: rangeForm.volume_min,
        volume_max: rangeForm.volume_max,
        adjust_key: rangeForm.adjust_key || null,
        remark: rangeForm.remark || null,
      }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.message || '范围调整未生效')
    }
    rangeVisible.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '范围调整失败'
  }
}

onMounted(() => {
  restoreUiState()
  void reload()
})
</script>
