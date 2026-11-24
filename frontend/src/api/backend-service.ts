import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type {
  ActionResult,
  EntryRow,
  MigrationResult,
  OverviewResult,
  PageResult,
  ReconcileResult,
} from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

/** 触发历史批次到 v2 整数口径的迁移（幂等，可重复执行）。 */
export async function runStatsMigration(migrationRun: string): Promise<MigrationResult> {
  const response = await request('/api/collection-stats/migrations', {
    method: 'POST',
    body: JSON.stringify({ migration_run: migrationRun }),
  })
  if (!response.ok) throw new Error('统计迁移执行失败')
  return await response.json()
}

/** 定时对账：v2 走整数合计与迁移完整度，v1 保持旧文本行数口径。 */
export async function fetchReconcile(
  version: 'v1' | 'v2',
  start: string,
  end: string,
): Promise<ReconcileResult> {
  const params = new URLSearchParams({ version, start, end }).toString()
  const response = await request(`/api/collection-stats/reconcile?${params}`)
  if (!response.ok) throw new Error('对账数据读取失败')
  return await response.json()
}

/** 修正旧批占位文本并重试迁移；v1 原值同步更新，旧 CSV 仍可按原值回查。 */
export async function correctLegacyText(
  batchId: string,
  rawCount: string,
  validCount: string,
): Promise<{ ok: boolean; 批次标识: string }> {
  const response = await request(`/api/collection-stats/batches/${batchId}/corrections`, {
    method: 'POST',
    body: JSON.stringify({ 原始行数: rawCount, 有效行数: validCount }),
  })
  if (!response.ok) throw new Error('占位文本修正失败')
  return await response.json()
}
