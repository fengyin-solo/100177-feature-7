<template>
  <section class="page" data-module="vessel-detail">
    <header class="page-head">
      <div>
        <h2>压力容器详情</h2>
        <p class="page-desc">只读展示容器台账与合规判定史；停用或维护封闭的容器在此仅可查看。</p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="goBack">返回合规视图</button>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>

    <template v-if="entry">
      <div class="stat-row">
        <article class="stat-card">
          <span class="stat-label">容器编号</span>
          <strong class="stat-value">{{ entry['容器编号'] }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">当前状态</span>
          <strong class="stat-value">{{ entry.status }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">最新合规判定</span>
          <strong class="stat-value" :class="latestClass">{{ latest?.result ?? '待补全' }}</strong>
        </article>
      </div>

      <table class="data-table detail-table">
        <tbody>
          <tr v-for="field in detailFields" :key="field">
            <th>{{ field }}</th>
            <td>{{ entry[field] || '—' }}</td>
          </tr>
        </tbody>
      </table>

      <h3 class="group-title">合规判定史（只追加，历史结论按当时范围保留）</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>判定时间</th>
            <th>范围版本</th>
            <th>判定结论</th>
            <th>判定来源</th>
            <th>说明</th>
            <th>当时允许范围</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in judgments" :key="item.id">
            <td>{{ item.judged_at }}</td>
            <td>{{ item.version }}</td>
            <td><span class="tag" :class="tagClass(item.result)">{{ item.result }}</span></td>
            <td>{{ item.source }}</td>
            <td>{{ item.reason }}</td>
            <td class="range-cell">
              压力 {{ fmt(item.range_snapshot.pressure_min) }}~{{ fmt(item.range_snapshot.pressure_max) }} MPa<br />
              容积 {{ fmt(item.range_snapshot.volume_min) }}~{{ fmt(item.range_snapshot.volume_max) }} m³
            </td>
          </tr>
          <tr v-if="!judgments.length">
            <td colspan="6" class="empty-state">暂无判定记录</td>
          </tr>
        </tbody>
      </table>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { fetchJson } from '@/api/client'

type Judgment = {
  id: number
  version: string
  result: '合规' | '超范围' | '待补全'
  reason: string
  source: string
  judged_at: string
  range_snapshot: Record<string, number>
}

const route = useRoute()
const router = useRouter()

const detailFields = ['容器编号', '容器名称', '设计压力', '容积规格', '介质类别', '使用场所', '下次检验日', '容器状态']
const entry = ref<Record<string, string | null> | null>(null)
const judgments = ref<Judgment[]>([])
const errorMessage = ref('')

const latest = computed(() => judgments.value[judgments.value.length - 1])
const latestClass = computed(() => {
  const result = latest.value?.result
  if (result === '超范围') return 'stat-danger'
  if (result === '待补全') return 'stat-warn'
  return ''
})

function fmt(value: number): string {
  return Number(value).toString()
}

function tagClass(result: Judgment['result']): string {
  if (result === '超范围') return 'tag tag-over'
  if (result === '待补全') return 'tag tag-pending'
  return 'tag tag-ok'
}

function goBack() {
  void router.push('/vessel')
}

onMounted(async () => {
  const id = Number(route.params.id)
  try {
    entry.value = await fetchJson<Record<string, string | null>>(`/api/vessel/${id}`)
    const payload = await fetchJson<{ items: Judgment[] }>(`/api/vessel/${id}/judgments`)
    judgments.value = payload.items
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '容器详情读取失败'
  }
})
</script>
