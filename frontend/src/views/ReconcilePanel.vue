<template>
  <section class="reconcile-panel" data-testid="reconcile-panel">
    <header class="reconcile-head">
      <h3>版本化采集统计 · 定时对账</h3>
      <p class="page-desc">
        v2 按整数口径汇总原始/有效总行数；回填期间若当日仍有待核查批次，会提示等待完整迁移并给出确切可用范围。
        v1 保持旧文本行数口径，供既有对账表继续使用。
      </p>
    </header>

    <form class="reconcile-controls" @submit.prevent="loadReconcile">
      <label class="filter-item">
        <span>开始日期</span>
        <input v-model="start" data-testid="reconcile-start" type="date" required />
      </label>
      <label class="filter-item">
        <span>结束日期</span>
        <input v-model="end" data-testid="reconcile-end" type="date" required />
      </label>
      <label class="filter-item">
        <span>统计口径</span>
        <select v-model="version" data-testid="reconcile-version">
          <option value="v2">v2 整数口径</option>
          <option value="v1">v1 文本口径</option>
        </select>
      </label>
      <button class="btn primary" data-testid="reconcile-query" type="submit">对账取数</button>
      <button class="btn" data-testid="run-migration" type="button" :disabled="migrating" @click="triggerMigration">
        {{ migrating ? '迁移中…' : '执行历史迁移' }}
      </button>
    </form>

    <p v-if="message" class="error-text" data-testid="reconcile-message">{{ message }}</p>

    <div v-if="result" class="reconcile-body">
      <div class="stat-row">
        <article class="stat-card">
          <span class="stat-label">原始总行数</span>
          <!-- 十进制字符串直接渲染，大整数不经过 Number，保留完整数位 -->
          <strong class="stat-value big-int" data-testid="raw-total">{{ result.原始总行数 || '0' }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">有效总行数</span>
          <strong class="stat-value big-int" data-testid="valid-total">{{ result.有效总行数 || '0' }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">统计口径</span>
          <strong class="stat-value">{{ result.统计版本 }}</strong>
        </article>
      </div>

      <template v-if="result.统计版本 === 'v2'">
        <p
          class="reconcile-status"
          :class="result.迁移完整 ? 'ok' : 'warn'"
          data-testid="migration-status"
        >
          {{ result.迁移完整 ? '当日迁移完整，合计可直接用于新版对账' : '当日仍在等待完整迁移' }}
          <template v-if="result.可用范围">
            ｜确切可用范围：{{ result.可用范围.start || '—' }} ~ {{ result.可用范围.end || '—' }}
          </template>
        </p>
        <table v-if="result.待核查批次?.length" class="data-table" data-testid="pending-table">
          <thead>
            <tr>
              <th>批次标识</th>
              <th>采集时间</th>
              <th>待核查原因</th>
              <th>来源原文（可回查，禁止按零处理）</th>
              <th>修正为整数后重试</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in result.待核查批次" :key="item.批次标识">
              <td>{{ item.批次标识 }}</td>
              <td>{{ item.采集时间 }}</td>
              <td>{{ item.待核查原因 }}</td>
              <td class="source-text">{{ formatSource(item.来源原文) }}</td>
              <td>
                <form class="correct-form" @submit.prevent="submitCorrection(item.批次标识)">
                  <input
                    v-model="corrections[item.批次标识].raw"
                    data-testid="correct-raw"
                    inputmode="numeric"
                    placeholder="原始行数"
                    required
                  />
                  <input
                    v-model="corrections[item.批次标识].valid"
                    data-testid="correct-valid"
                    inputmode="numeric"
                    placeholder="有效行数"
                    required
                  />
                  <button class="btn primary" data-testid="correct-submit" type="submit">
                    修正并重试
                  </button>
                </form>
              </td>
            </tr>
          </tbody>
        </table>
      </template>

      <template v-else>
        <p class="reconcile-status ok" data-testid="v1-note">
          v1 文本口径：占位文本不计入合计，原样列入下方回查清单，与旧 CSV 历史输出一致。
        </p>
        <table v-if="result.文本行批次?.length" class="data-table" data-testid="v1-text-table">
          <thead>
            <tr>
              <th>批次标识</th>
              <th>原始行数（原文）</th>
              <th>有效行数（原文）</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in result.文本行批次" :key="item.批次标识">
              <td>{{ item.批次标识 }}</td>
              <td class="source-text">{{ item.原始行数 }}</td>
              <td class="source-text">{{ item.有效行数 }}</td>
            </tr>
          </tbody>
        </table>
      </template>
    </div>
  </section>
</template>

<script setup lang="ts">
import { ref } from 'vue'

import {
  correctLegacyText,
  fetchReconcile,
  runStatsMigration,
} from '@/api/backend-service'
import type { MigrationResult, ReconcileResult } from '@/data/types'

const today = new Date().toISOString().slice(0, 10)
const start = ref(today)
const end = ref(today)
const version = ref<'v1' | 'v2'>('v2')
const result = ref<ReconcileResult | null>(null)
const message = ref('')
const migrating = ref(false)
// 待核查批次的修正输入：全程字符串，大整数也不会被 Number 截断
const corrections = ref<Record<string, { raw: string; valid: string }>>({})

function formatSource(source: Record<string, string> | undefined): string {
  if (!source) return '—'
  return Object.entries(source).map(([key, value]) => `${key}=${value}`).join('；')
}

async function loadReconcile() {
  message.value = ''
  try {
    result.value = await fetchReconcile(version.value, start.value, end.value)
    for (const item of result.value.待核查批次 ?? []) {
      if (!corrections.value[item.批次标识]) {
        corrections.value[item.批次标识] = { raw: '', valid: '' }
      }
    }
  } catch (error) {
    message.value = error instanceof Error ? error.message : '对账取数失败'
  }
}

async function submitCorrection(batchId: string) {
  const draft = corrections.value[batchId]
  message.value = ''
  try {
    await correctLegacyText(batchId, draft.raw.trim(), draft.valid.trim())
    await loadReconcile()
    message.value = `批次 ${batchId} 占位文本已修正并完成 v2 迁移`
  } catch (error) {
    message.value = error instanceof Error ? error.message : '修正失败'
  }
}

async function triggerMigration() {
  migrating.value = true
  message.value = ''
  try {
    const summary: MigrationResult = await runStatsMigration(`ui-${Date.now()}`)
    message.value = `迁移完成：新迁移 ${summary.migrated_count} 批，跳过已迁移 ${summary.skipped_migrated.length} 批，待核查 ${summary.pending_count} 批`
    await loadReconcile()
  } catch (error) {
    message.value = error instanceof Error ? error.message : '迁移失败'
  } finally {
    migrating.value = false
  }
}
</script>

<style scoped>
.reconcile-panel {
  border: 1px solid var(--border-color, #d9dde5);
  border-radius: 10px;
  padding: 16px;
  margin-bottom: 20px;
  background: #fbfcfe;
}
.reconcile-controls {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: flex-end;
  margin: 12px 0;
}
.reconcile-status {
  margin: 10px 0;
  font-weight: 600;
}
.reconcile-status.ok {
  color: #1a7f37;
}
.reconcile-status.warn {
  color: #b26a00;
}
.big-int {
  font-variant-numeric: tabular-nums;
  word-break: break-all;
}
.source-text {
  color: #8a4b00;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.correct-form {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.correct-form input {
  width: 110px;
}
</style>
