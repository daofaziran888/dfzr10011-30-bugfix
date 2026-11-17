<template>
  <section class="page" data-module="robot">
    <header class="page-head">
      <div>
        <h2>RPA执行记录管理</h2>
        <p class="page-desc">维护RPA执行的流程关联、节点分配与结果追踪，支持执行失败记录的批量重试续做与审计核对。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记执行记录</button>
        <button class="btn" type="button" @click="exportRows">导出RPA执行记录清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>记录编号</span>
        <input v-model="filters.keyword" placeholder="按记录编号检索" />
      </label>
      <label class="filter-item">
        <span>记录状态</span>
        <select v-model="filters.status">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <div class="batch-bar">
      <strong>批量重试</strong>
      <span>已选 {{ selectedIds.length }} 条执行失败记录</span>
      <label class="strategy-item">
        <input v-model="strategy" type="radio" value="wait_callback" />
        等待回调后释放执行权
      </label>
      <label class="strategy-item">
        <input v-model="strategy" type="radio" value="release_first" />
        先释放批次执行权
      </label>
      <button class="btn primary" type="button" :disabled="!selectedIds.length || submitting" @click="submitBatch">
        {{ submitting ? '提交中…' : '整组提交批量重试' }}
      </button>
      <span class="hint">
        等待回调：批次暂不结束，节点占用与等待记录同时可见；先释放：批次立即完成，迟到的完成回调记为晚到回调轨迹。
      </span>
    </div>

    <div v-if="submitResults.length" class="result-panel">
      <strong>{{ submitSummary }}</strong>
      <ul>
        <li v-for="item in submitResults" :key="`${item.记录id}-${item.message}`" :class="item.ok ? 'result-ok' : 'result-fail'">
          {{ item.记录编号 }}：{{ item.message }}
        </li>
      </ul>
      <p class="hint">未通过的记录已保留在勾选框中，处理后可再次整组提交。</p>
    </div>

    <table class="data-table">
      <thead>
        <tr>
          <th>
            <input
              type="checkbox"
              :checked="allFailedSelected"
              :disabled="!failedRows.length"
              title="全选当前页执行失败记录"
              @change="toggleSelectAll"
            />
          </th>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td>
            <input
              type="checkbox"
              :disabled="row.status !== '执行失败'"
              :checked="selectedSet.has(Number(row.id))"
              :title="row.status === '执行失败' ? '勾选进入批量重试' : '只有执行失败记录能进入重试'"
              @change="toggleSelect(Number(row.id))"
            />
          </td>
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td><span class="status-tag" :data-status="row.status">{{ row.status }}</span></td>
          <td class="row-actions">
            <button class="link" type="button" @click="openDetail(row)">详情</button>
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
          <td :colspan="columns.length + 3" class="empty-state">暂无RPA执行记录数据，可先登记执行记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条RPA执行记录记录</span>
      <span v-if="noticeMessage" class="notice-text">{{ noticeMessage }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <section class="panel">
      <div class="panel-head">
        <h3>重试批次</h3>
        <span class="hint">按批次核对重试记录与影响范围；执行中的批次可见节点占用与等待记录</span>
      </div>
      <table class="data-table">
        <thead>
          <tr>
            <th>批次编号</th>
            <th>释放策略</th>
            <th>批次状态</th>
            <th>记录数</th>
            <th>待回调</th>
            <th>晚到回调</th>
            <th>提交时间</th>
            <th>完成时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="batch in batches" :key="batch.id">
            <tr>
              <td>{{ batch.批次编号 }}</td>
              <td>{{ batch.策略说明 }}</td>
              <td><span class="status-tag" :data-status="batch.status">{{ batch.status }}</span></td>
              <td>{{ batch.items?.length ?? 0 }}</td>
              <td>{{ waitingItems(batch).length }}</td>
              <td>{{ batch.晚到回调?.length ?? 0 }}</td>
              <td>{{ batch.提交时间 }}</td>
              <td>{{ batch.完成时间 ?? '—' }}</td>
              <td>
                <button class="link" type="button" @click="toggleBatch(batch.id)">
                  {{ expandedBatch === batch.id ? '收起' : '核对' }}
                </button>
              </td>
            </tr>
            <tr v-if="expandedBatch === batch.id">
              <td colspan="9" class="batch-detail">
                <p v-if="batch.status === '执行中'" class="hint">
                  节点占用：{{ occupiedNodes(batch).join('、') || '—' }}；
                  等待记录：{{ waitingItems(batch).map((item) => item.记录编号).join('、') || '—' }}
                </p>
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
                      <th>模拟回调</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="item in batch.items" :key="item.记录id">
                      <td>{{ item.记录编号 }}</td>
                      <td>{{ item.流程模板 }}</td>
                      <td>{{ item.执行节点 }}</td>
                      <td>{{ item.失败步骤 }}</td>
                      <td>{{ item.版本号 }}</td>
                      <td>{{ item.结果 }}</td>
                      <td>{{ item.回调时间 ?? '—' }}</td>
                      <td class="row-actions">
                        <template v-if="item.结果 === '待回调'">
                          <button class="link" type="button" @click="sendCallback(batch, item, '成功')">回调成功</button>
                          <button class="link" type="button" @click="sendCallback(batch, item, '失败')">回调失败</button>
                        </template>
                        <span v-else>—</span>
                      </td>
                    </tr>
                  </tbody>
                </table>
                <div v-if="batch.晚到回调?.length" class="late-list">
                  <strong>晚到回调轨迹</strong>
                  <ul>
                    <li v-for="(trail, index) in batch.晚到回调" :key="index">
                      {{ trail.回调时间 }} · {{ trail.记录编号 }} · {{ trail.结果 }} · {{ trail.说明 }}
                    </li>
                  </ul>
                </div>
              </td>
            </tr>
          </template>
          <tr v-if="!batches.length">
            <td colspan="9" class="empty-state">暂无重试批次，勾选执行失败记录后整组提交</td>
          </tr>
        </tbody>
      </table>
    </section>

    <div v-if="detail" class="drawer-mask" @click.self="closeDetail">
      <aside class="drawer">
        <header class="drawer-head">
          <h3>执行记录详情</h3>
          <button class="link" type="button" @click="closeDetail">关闭</button>
        </header>
        <dl class="detail-list">
          <template v-for="field in detailFields" :key="field">
            <dt>{{ field }}</dt>
            <dd>{{ detailValue(field) }}</dd>
          </template>
        </dl>
      </aside>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>
type Batch = Record<string, any>
type BatchItem = Record<string, any>
type ResultItem = { 记录id: number | string; 记录编号: string; ok: boolean; message: string }

const ENDPOINT = '/api/robot'
const columns = ["记录编号", "流程模板", "执行节点", "失败步骤", "开始时间", "结束时间", "执行结果", "异常信息"]
const actions = ["启动执行", "重试执行", "归档记录"]
const statuses = ["待执行", "执行中", "执行成功", "执行失败"]
const detailFields = ["记录编号", "流程模板", "执行节点", "失败步骤", "版本号", "开始时间", "结束时间", "执行结果", "异常信息", "记录状态", "status", "占用批次", "重试批次"]

const stats = ref([
  { label: "执行记录总数", value: 0 },
  { label: "执行中记录", value: 0 },
  { label: "执行失败待续做", value: 0 },
])

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const noticeMessage = ref('')
const filters = ref<Record<string, string>>({ keyword: '', status: '' })

const selectedIds = ref<number[]>([])
const strategy = ref<'wait_callback' | 'release_first'>('wait_callback')
const submitting = ref(false)
const requestId = ref(crypto.randomUUID())
const submitResults = ref<ResultItem[]>([])
const submitSummary = ref('')

const batches = ref<Batch[]>([])
const expandedBatch = ref<number | null>(null)

const detail = ref<Row | null>(null)

const selectedSet = computed(() => new Set(selectedIds.value))
const failedRows = computed(() => rows.value.filter((row) => row.status === '执行失败'))
const allFailedSelected = computed(
  () => failedRows.value.length > 0 && failedRows.value.every((row) => selectedSet.value.has(Number(row.id))),
)

// 勾选或策略变化后换新的提交标识；提交响应前重复点击沿用同一标识，服务端幂等，不生成第二份执行审计
watch([selectedIds, strategy], () => {
  requestId.value = crypto.randomUUID()
})

function toggleSelect(id: number) {
  selectedIds.value = selectedIds.value.includes(id)
    ? selectedIds.value.filter((item) => item !== id)
    : [...selectedIds.value, id]
}

function toggleSelectAll() {
  if (allFailedSelected.value) {
    const pageIds = new Set(failedRows.value.map((row) => Number(row.id)))
    selectedIds.value = selectedIds.value.filter((id) => !pageIds.has(id))
  } else {
    // 用 Set 合并去重，避免重试记录重复显示
    const merged = new Set([...selectedIds.value, ...failedRows.value.map((row) => Number(row.id))])
    selectedIds.value = [...merged]
  }
}

function resetFilters() {
  filters.value = { keyword: '', status: '' }
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '执行记录登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      throw new Error(payload.message || 'RPA执行记录动作未生效，请稍后重试')
    }
    noticeMessage.value = payload.message ?? ''
    await Promise.all([reload(), loadStats()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'RPA执行记录操作失败'
  }
}

async function submitBatch() {
  if (!selectedIds.value.length || submitting.value) {
    return
  }
  submitting.value = true
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/batch-retry`, {
      method: 'POST',
      body: JSON.stringify({
        values: { ids: selectedIds.value, strategy: strategy.value, request_id: requestId.value },
      }),
    })
    const payload = await response.json()
    submitResults.value = (payload.results ?? []) as ResultItem[]
    submitSummary.value = payload.message ?? ''
    // 保留失败项在勾选框中，修正后可再次整组提交
    selectedIds.value = submitResults.value
      .filter((item) => !item.ok)
      .map((item) => Number(item.记录id))
      .filter((id) => !Number.isNaN(id))
    if (!payload.ok && !submitResults.value.length) {
      throw new Error(payload.message || '批量重试未受理')
    }
    await Promise.all([reload(), loadBatches(), loadStats()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '批量重试提交失败'
  } finally {
    submitting.value = false
  }
}

async function sendCallback(batch: Batch, item: BatchItem, result: string) {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/batch-retry/${batch.id}/callback`, {
      method: 'POST',
      body: JSON.stringify({ values: { record_id: item.记录id, result } }),
    })
    const payload = await response.json()
    noticeMessage.value = payload.message ?? ''
    if (!payload.ok) {
      throw new Error(payload.message || '回调未受理')
    }
    await Promise.all([reload(), loadBatches(), loadStats()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '完成回调提交失败'
  }
}

function waitingItems(batch: Batch): BatchItem[] {
  return (batch.items ?? []).filter((item: BatchItem) => item.结果 === '待回调')
}

function occupiedNodes(batch: Batch): string[] {
  const nodes = waitingItems(batch).map((item) => String(item.执行节点 ?? '')).filter(Boolean)
  return [...new Set(nodes)]
}

function toggleBatch(id: number) {
  expandedBatch.value = expandedBatch.value === id ? null : id
}

async function openDetail(row: Row) {
  errorMessage.value = ''
  try {
    // 按记录 id 重新读取详情，字段与列表一一对应，避免列表与详情串位
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) {
      throw new Error(`执行记录 ${row.记录编号 ?? row.id} 详情读取失败`)
    }
    detail.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '执行记录详情读取失败'
  }
}

function closeDetail() {
  detail.value = null
}

function detailValue(field: string): string {
  const value = detail.value?.[field]
  if (value === undefined || value === null || value === '') {
    return '—'
  }
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (filters.value.keyword) {
    query.set('keyword', filters.value.keyword)
  }
  if (filters.value.status) {
    query.set('status', filters.value.status)
  }
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) {
      throw new Error('执行记录列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'RPA执行记录列表读取失败'
  }
}

async function loadBatches() {
  try {
    const response = await request(`${ENDPOINT}/batch-retry?size=100`)
    if (!response.ok) {
      throw new Error('重试批次读取失败')
    }
    const payload = await response.json()
    // 按批次 id 去重，避免重试记录重复显示
    const deduped = new Map<number, Batch>()
    for (const item of payload.items ?? []) {
      deduped.set(Number(item.id), item)
    }
    batches.value = [...deduped.values()]
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '重试批次读取失败'
  }
}

async function loadStats() {
  try {
    const [allRes, runningRes, failedRes] = await Promise.all([
      request(`${ENDPOINT}?size=1`),
      request(`${ENDPOINT}?status=${encodeURIComponent('执行中')}&size=1`),
      request(`${ENDPOINT}?status=${encodeURIComponent('执行失败')}&size=1`),
    ])
    const [all, running, failed] = await Promise.all([allRes.json(), runningRes.json(), failedRes.json()])
    stats.value = [
      { label: '执行记录总数', value: all.total ?? 0 },
      { label: '执行中记录', value: running.total ?? 0 },
      { label: '执行失败待续做', value: failed.total ?? 0 },
    ]
  } catch {
    // 统计读取失败不阻塞页面
  }
}

onMounted(() => {
  void reload()
  void loadBatches()
  void loadStats()
})
</script>
