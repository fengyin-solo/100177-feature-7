<template>
  <section class="page" data-module="vessel-detail">
    <header class="page-head">
      <div>
        <h2>压力容器详情</h2>
        <p class="page-desc">查看容器台账与全部合规判定记录；历史判定按当时允许范围保留，报废归档后不再被重判推翻。</p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="goBack">返回合规视图</button>
      </div>
    </header>

    <div v-if="errorMessage" class="page-foot"><span class="error-text">{{ errorMessage }}</span></div>

    <template v-else-if="entry">
      <div class="stat-row">
        <article class="stat-card">
          <span class="stat-label">容器编号</span>
          <strong class="stat-value small">{{ entry['容器编号'] }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">容器状态</span>
          <strong class="stat-value small">{{ entry.status }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">当前合规结论</span>
          <strong class="stat-value small">
            <span class="badge" :class="badgeClass">{{ entry.compliance.result }}</span>
          </strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">判定范围版本</span>
          <strong class="stat-value small">v{{ entry.compliance.range_revision }}</strong>
        </article>
      </div>

      <table class="data-table">
        <tbody>
          <tr><th>容器名称</th><td>{{ entry['容器名称'] ?? '—' }}</td><th>介质类别</th><td>{{ entry['介质类别'] ?? '—' }}</td></tr>
          <tr>
            <th>设计压力（MPa）</th>
            <td>{{ entry.compliance.pressure.text ?? '—' }}（允许 {{ entry.compliance.pressure.min }}～{{ entry.compliance.pressure.max }}）</td>
            <th>容积规格（m³）</th>
            <td>{{ entry.compliance.volume.text ?? '—' }}（允许 {{ entry.compliance.volume.min }}～{{ entry.compliance.volume.max }}）</td>
          </tr>
          <tr><th>使用场所</th><td>{{ entry['使用场所'] ?? '—' }}</td><th>下次检验日</th><td>{{ entry['下次检验日'] ?? '—' }}</td></tr>
        </tbody>
      </table>

      <h3 class="section-title">合规判定历史（{{ records.length }} 条）</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>判定时间</th><th>结论</th><th>触发来源</th><th>适用范围版本</th>
            <th>当时范围（压力/容积）</th><th>说明</th><th>归档</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="record in records" :key="record.id">
            <td>{{ record.effective_at }}</td>
            <td><span class="badge" :class="recordClass(record.result)">{{ record.result }}</span></td>
            <td>{{ record.source }}</td>
            <td>v{{ record.range_revision }}</td>
            <td class="muted">
              {{ record.range_snapshot.pressure_min }}～{{ record.range_snapshot.pressure_max }} MPa /
              {{ record.range_snapshot.volume_min }}～{{ record.range_snapshot.volume_max }} m³
            </td>
            <td>
              <span v-if="!record.reasons.length">设计压力与容积规格均在允许范围内</span>
              <p v-for="(reason, index) in record.reasons" :key="index" class="reason-text">{{ reason }}</p>
            </td>
            <td>{{ record.archived ? '已归档' : '—' }}</td>
          </tr>
          <tr v-if="!records.length">
            <td colspan="7" class="empty-state">暂无判定记录</td>
          </tr>
        </tbody>
      </table>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { request } from '@/api/client'

type RecordItem = {
  id: number
  result: '合规' | '超范围' | '待补全'
  reasons: string[]
  source: string
  range_revision: number
  range_snapshot: { pressure_min: number; pressure_max: number; volume_min: number; volume_max: number }
  effective_at: string
  archived: boolean
}
type SpecInfo = {
  text: string | number | null
  min: number
  max: number
}
type Entry = {
  id: number
  status: string
  compliance: { result: RecordItem['result']; range_revision: number; pressure: SpecInfo; volume: SpecInfo }
} & Record<string, string | number | null>

const route = useRoute()
const router = useRouter()
const entry = ref<Entry | null>(null)
const records = ref<RecordItem[]>([])
const errorMessage = ref('')

const entryId = computed(() => Number(route.params.id))

const badgeClass = computed(() => recordClass(entry.value?.compliance.result ?? ''))

function recordClass(result: string) {
  if (result === '合规') return 'row-ok'
  if (result === '超范围') return 'row-over'
  return 'row-incomplete'
}

function goBack() {
  void router.push('/vessel')
}

onMounted(async () => {
  try {
    const [detailRes, recordsRes] = await Promise.all([
      request(`/api/vessel/${entryId.value}`),
      request(`/api/vessel/${entryId.value}/compliance/records`),
    ])
    if (!detailRes.ok) throw new Error(`压力容器 ${entryId.value} 不存在或已归档`)
    entry.value = (await detailRes.json()) as Entry
    if (recordsRes.ok) {
      const payload = (await recordsRes.json()) as { items: RecordItem[] }
      records.value = payload.items ?? []
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '详情读取失败'
  }
})
</script>
