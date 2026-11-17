<template>
  <section class="page" data-module="audit">
    <header class="page-head">
      <div>
        <h2>采集操作审计管理</h2>
        <p class="page-desc">维护采集操作的类型分类、详情记录与复核追踪，并按重试批次核对修复RPA批量重的执行节点、晚到回调与影响范围。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记审计记录</button>
        <button class="btn" type="button" @click="exportRows">导出采集操作审计清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <section class="panel">
      <div class="panel-head">
        <h3>修复RPA批量重待处理清单</h3>
        <div class="panel-actions">
          <span v-if="retryMessage" class="notice-text">{{ retryMessage }}</span>
          <button class="btn" type="button" :disabled="syncing" @click="syncRetry">
            {{ syncing ? '同步中…' : '同步刷新' }}
          </button>
        </div>
      </div>
      <table class="data-table">
        <thead>
          <tr>
            <th>批次编号</th>
            <th>批次状态</th>
            <th>释放策略</th>
            <th>执行节点</th>
            <th>影响范围</th>
            <th>晚到回调</th>
            <th>待处理</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="row in retryRows" :key="row.id">
            <tr>
              <td>{{ row.批次编号 }}</td>
              <td><span class="status-tag" :data-status="row.批次状态">{{ row.批次状态 }}</span></td>
              <td>{{ row.策略说明 }}</td>
              <td>{{ (row.执行节点 ?? []).join('、') || '—' }}</td>
              <td>{{ row.影响范围 }}</td>
              <td>{{ (row.晚到回调 ?? []).length }}</td>
              <td>{{ row.pending ? '待处理' : '已办结' }}</td>
              <td>
                <button class="link" type="button" @click="toggleRetryDetail(row.id)">
                  {{ retryExpanded === row.id ? '收起' : '明细' }}
                </button>
              </td>
            </tr>
            <tr v-if="retryExpanded === row.id">
              <td colspan="8" class="batch-detail">
                <table class="data-table sub-table">
                  <thead>
                    <tr>
                      <th>记录编号</th>
                      <th>流程模板</th>
                      <th>执行节点</th>
                      <th>失败步骤</th>
                      <th>版本号</th>
                      <th>续做结果</th>
                      <th>回调时间</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="item in row.重试记录 ?? []" :key="item.记录id">
                      <td>{{ item.记录编号 }}</td>
                      <td>{{ item.流程模板 }}</td>
                      <td>{{ item.执行节点 }}</td>
                      <td>{{ item.失败步骤 }}</td>
                      <td>{{ item.版本号 }}</td>
                      <td>{{ item.结果 }}</td>
                      <td>{{ item.回调时间 ?? '—' }}</td>
                    </tr>
                  </tbody>
                </table>
                <div v-if="(row.晚到回调 ?? []).length" class="late-list">
                  <strong>晚到回调轨迹</strong>
                  <ul>
                    <li v-for="(trail, index) in row.晚到回调" :key="index">
                      {{ trail.回调时间 }} · {{ trail.记录编号 }} · {{ trail.结果 }} · {{ trail.说明 }}
                    </li>
                  </ul>
                </div>
                <p class="hint">{{ row.操作详情 }}</p>
              </td>
            </tr>
          </template>
          <tr v-if="!retryRows.length">
            <td colspan="8" class="empty-state">暂无修复RPA批量重审计记录，可先在RPA执行记录页整组提交批量重试</td>
          </tr>
        </tbody>
      </table>
    </section>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无采集操作审计数据，可先登记审计记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条采集操作审计记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>

const ENDPOINT = '/api/audit'
const columns = ["记录编号", "操作类型", "操作对象", "操作人", "操作时间", "操作详情", "影响范围", "复核状态"]
const actions = ["标记复核", "归档记录"]
const statuses = ["已记录", "已复核", "已归档"]
const stats = [{"label": "审计记录总数", "value": 0}, {"label": "已复核记录", "value": 0}, {"label": "已归档记录", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

const retryRows = ref<Row[]>([])
const retryExpanded = ref<number | null>(null)
const retryMessage = ref('')
const syncing = ref(false)

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '审计记录登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      throw new Error(payload.message || '采集操作审计动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集操作审计操作失败'
  }
}

function toggleRetryDetail(id: number) {
  retryExpanded.value = retryExpanded.value === id ? null : id
}

async function syncRetry() {
  if (syncing.value) {
    return
  }
  syncing.value = true
  retryMessage.value = ''
  try {
    // 另一个入口：同步刷新修复RPA批量重清单与失败续做结果数据
    const response = await request(`${ENDPOINT}/sync-robot-retry`, {
      method: 'POST',
      body: JSON.stringify({ values: {} }),
    })
    const payload = await response.json()
    retryMessage.value = payload.message ?? ''
    await Promise.all([loadRetryBatches(), reload()])
  } catch (error) {
    retryMessage.value = error instanceof Error ? error.message : '同步刷新失败'
  } finally {
    syncing.value = false
  }
}

async function loadRetryBatches() {
  try {
    const response = await request(`${ENDPOINT}/robot-retry-batches?size=100`)
    if (!response.ok) {
      throw new Error('修复RPA批量重清单读取失败')
    }
    const payload = await response.json()
    retryRows.value = payload.items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '修复RPA批量重清单读取失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('审计记录列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集操作审计列表读取失败'
  }
}

onMounted(() => {
  void reload()
  void loadRetryBatches()
})
</script>
