"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。

导出通道同时关联两套统计口径：

- v2（默认）：从 ``collection_stats`` 取批次整数行数，并按批次采集日计算
  同日范围内的原始/有效总行数，全程十进制字符串输出，大整数不丢数位；
- v1：旧批次回执仍给出 ``result_store`` 里的文本行数，与旧 CSV 完全一致。

v1 的 CSV 列保持不变，版本化统计只挂在行内扩展字段上，不进入清单 CSV。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService

STAT_VERSION_V1 = "v1"
STAT_VERSION_V2 = "v2"


class ExportChannelService(BaseModuleService):
    MODULE = "export_channel"
    ENTITY = "导出任务"
    OPERATION_TERM = "采集"
    LIST_FIELDS = ["任务标识", "来源批次", "导出格式", "目标路径", "字段筛选", "导出状态", "文件大小", "完成时间"]
    REQUIRED_FIELDS = ["任务标识", "来源批次", "导出格式"]
    STATUS_ORDER = ["排队中", "导出中", "已完成", "导出失败"]
    ACTION_RULES = {"启动导出": "排队中", "取消导出": "导出中"}
    NEGATIVE_ACTIONS = []

    def _find_legacy_batch(self, batch_id: str) -> dict[str, Any] | None:
        from app.store import store
        for row in store.rows("result_store"):
            if str(row.get("批次标识", "")) == batch_id:
                return row
        return None

    def build_export_stats(self, batch_id: str,
                           stat_version: str = STAT_VERSION_V2) -> dict[str, Any]:
        """汇总导出回执需要的版本化统计数据。"""
        from app.services.collection_stats import (
            CollectionStatsService, decimal_text,
        )
        stats_svc = CollectionStatsService()
        legacy = self._find_legacy_batch(batch_id)
        day = str(legacy.get("采集时间", "")) if legacy else ""
        v2_row = stats_svc.find_by_batch(batch_id)

        # 旧批回执始终保留 v1 文本行数（可能是占位文本，原样给出）
        v1_raw_text = "" if legacy is None else str(legacy.get("原始行数", ""))
        v1_valid_text = "" if legacy is None else str(legacy.get("有效行数", ""))

        if stat_version == STAT_VERSION_V1:
            return {
                "统计版本": STAT_VERSION_V1,
                "采集时间": day,
                "文本原始行数": v1_raw_text,
                "文本有效行数": v1_valid_text,
                "原始总行数": v1_raw_text,
                "有效总行数": v1_valid_text,
                "迁移状态": v2_row.get("迁移状态", "") if v2_row else "未迁移",
            }

        # v2：同日范围整数合计（待核查批次不计入，由对账状态提示）
        daily = stats_svc.v2_daily_reconcile(day, day) if day else {}
        if v2_row is None or v2_row.get("迁移状态") != "已迁移":
            return {
                "统计版本": STAT_VERSION_V2,
                "采集时间": day,
                "文本原始行数": v1_raw_text,
                "文本有效行数": v1_valid_text,
                "批次原始行数": "",
                "批次有效行数": "",
                "原始总行数": daily.get("原始总行数", "0"),
                "有效总行数": daily.get("有效总行数", "0"),
                "迁移状态": v2_row.get("迁移状态", "未迁移") if v2_row else "未迁移",
                "对账状态": "当日仍在等待完整迁移",
                "可用范围": daily.get("可用范围", {"start": "", "end": ""}),
            }
        return {
            "统计版本": STAT_VERSION_V2,
            "采集时间": day,
            "文本原始行数": v1_raw_text,
            "文本有效行数": v1_valid_text,
            "批次原始行数": decimal_text(v2_row.get("原始行数")),
            "批次有效行数": decimal_text(v2_row.get("有效行数")),
            "原始总行数": daily.get("原始总行数", "0"),
            "有效总行数": daily.get("有效总行数", "0"),
            "迁移状态": "已迁移",
            "对账状态": "当日迁移完整" if daily.get("迁移完整") else "当日仍在等待完整迁移",
            "可用范围": daily.get("可用范围", {"start": "", "end": ""}),
        }

    def domain_export(self, batch_id: str, export_format: str = "csv",
                      fields: list[str] | None = None,
                      stat_version: str = STAT_VERSION_V2) -> dict:
        """导出批次数据到指定格式，并关联版本化采集统计。"""
        from app.store import store
        import time
        rows = store.rows(self.MODULE)
        now = time.strftime("%Y-%m-%d")
        file_size = (len(batch_id) * 1024) + 512
        version = stat_version if stat_version in (STAT_VERSION_V1, STAT_VERSION_V2) else STAT_VERSION_V2
        stats = self.build_export_stats(batch_id, version)
        entry = {
            "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
            "任务标识": f"EXPORT-{batch_id}",
            "来源批次": batch_id,
            "导出格式": export_format,
            "目标路径": f"/exports/{batch_id}.{export_format}",
            "字段筛选": ",".join(fields or []),
            "导出状态": "已完成",
            "文件大小": f"{file_size}",
            "完成时间": now,
            "status": "已完成",
            "pending": False,
            "abnormal": False,
            # 版本化统计挂在扩展字段上，不进入 v1 清单 CSV 的列
            **stats,
        }
        rows.append(entry)
        return entry
