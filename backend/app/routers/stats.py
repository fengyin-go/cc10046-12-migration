"""版本化采集统计路由：历史迁移、按日对账与大整数安全的统计查询。

路由前缀固定在 ``/api/collection-stats`` 下，先于通用资源路由
``/api/{module}`` 注册，避免动态路径抢先匹配。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.collection_stats import (
    CollectionStatsService,
    STAT_VERSION_V1,
    STAT_VERSION_V2,
)

router = APIRouter(prefix="/api/collection-stats", tags=["版本化采集统计"])


class MigrationPayload(BaseModel):
    migration_run: str = Field(default="migration-default")


class CorrectionPayload(BaseModel):
    原始行数: str
    有效行数: str


class ExportStatsPayload(BaseModel):
    export_format: str = "csv"
    fields: list[str] = Field(default_factory=list)
    stat_version: str = STAT_VERSION_V2


@router.post("/migrations")
def run_migration(payload: MigrationPayload) -> dict[str, Any]:
    """触发一次历史批次到 v2 整数口径的迁移（可重复执行、幂等）。"""
    service = CollectionStatsService()
    return service.migrate_legacy_batches(payload.migration_run)


@router.get("/reconcile")
def reconcile(
    version: str = STAT_VERSION_V2,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """按日期范围做定时对账。

    v2 返回整数合计、迁移完整度与确切可用范围；v1 保持旧文本行数口径。
    """
    service = CollectionStatsService()
    normalized = STAT_VERSION_V1 if version == STAT_VERSION_V1 else STAT_VERSION_V2
    return service.reconcile(normalized, start, end)


@router.get("/batches/{batch_id}")
def batch_stat(batch_id: str) -> dict[str, Any]:
    """按批次标识查询单条版本化统计（含待核查来源原文）。"""
    service = CollectionStatsService()
    row = service.find_by_batch(batch_id)
    if row is None:
        return {
            "批次标识": batch_id,
            "统计版本": STAT_VERSION_V2,
            "迁移状态": "未迁移",
            "原始行数": "",
            "有效行数": "",
            "来源原文": {},
        }
    return service.to_wire(row)


@router.post("/batches/{batch_id}/corrections")
def correct_legacy_text(batch_id: str, payload: CorrectionPayload) -> dict[str, Any]:
    """人工修正旧批占位文本后重试迁移。

    修正直接落在 ``result_store`` 的 v1 文本字段上（保留旧 CSV 可回查的原值），
    随后重新执行迁移，把待核查批次转为已迁移的 v2 整数统计。
    """
    from app.store import store

    legacy = next(
        (row for row in store.rows("result_store")
         if str(row.get("批次标识", "")) == batch_id),
        None,
    )
    if legacy is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"批次 {batch_id} 不存在")
    legacy["原始行数"] = payload.原始行数
    legacy["有效行数"] = payload.有效行数
    service = CollectionStatsService()
    summary = service.migrate_legacy_batches(f"correction-{batch_id}")
    return {
        "ok": True,
        "批次标识": batch_id,
        "迁移结果": summary,
        "统计": service.to_wire(service.find_by_batch(batch_id)),
    }


@router.post("/batches/{batch_id}/exports")
def export_with_stats(batch_id: str, payload: ExportStatsPayload) -> dict[str, Any]:
    """按批次发起导出，回执携带版本化采集统计（v2 整数合计 / v1 文本行数）。"""
    from app.services.export_channel import ExportChannelService

    version = payload.stat_version if payload.stat_version in (
        STAT_VERSION_V1, STAT_VERSION_V2) else STAT_VERSION_V2
    return ExportChannelService().domain_export(
        batch_id, payload.export_format, payload.fields, stat_version=version)
