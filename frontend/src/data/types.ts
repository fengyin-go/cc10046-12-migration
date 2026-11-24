/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

/** v2 整数口径对账结果；所有行数均为十进制字符串，避免 JS 大整数精度丢失。 */
export type ReconcileResult = {
  统计版本: string
  开始日期: string
  结束日期: string
  迁移完整?: boolean
  原始总行数: string
  有效总行数: string
  已迁移批次数?: number
  待核查批次数?: number
  批次数?: number
  可用范围?: { start: string; end: string }
  待核查日期?: string[]
  待核查批次?: {
    批次标识: string
    采集时间: string
    待核查原因: string
    来源原文: Record<string, string>
  }[]
  文本行批次?: { 批次标识: string; 原始行数: string; 有效行数: string }[]
}

export type MigrationResult = {
  migration_run: string
  total_legacy: number
  migrated: string[]
  skipped_migrated: string[]
  pending_review: { 批次标识: string; 待核查原因: string }[]
  migrated_count: number
  pending_count: number
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}
