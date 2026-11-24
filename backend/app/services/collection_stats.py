"""版本化采集统计（v2 整数口径）与历史批次迁移服务。

背景
----
旧版（v1）把批次行数写在 ``result_store`` 的 ``原始行数`` / ``有效行数``
文本字段里，里面既有正常的十进制文本，也有不具备数字含义的占位文本
（例如 ``"结果存储样例1"``）。新版（v2）要求以任意精度整数承载行数，
并且：

- v1 的列表 / CSV 必须保持原文本格式，供仍依赖 v1 的定时对账使用；
- 迁移失败的旧批要保留来源文本并标记「待核查」，不能当成 0；
- 同一次迁移重复执行时，已经确认迁移的批次只计一次（幂等）；
- 超过浮点 / JS 安全整数范围的数值全程按十进制字符串传递，保留完整数位。

本模块只依赖内存仓库与标准库，统计值在内存中用 Python ``int`` 保存，
对外一律转成十进制字符串，避免 JSON 数字被前端 ``Number`` 截断。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store

# 统计口径版本
STAT_VERSION_V1 = "v1"
STAT_VERSION_V2 = "v2"

# 迁移状态
STATUS_PENDING_REVIEW = "待核查"
STATUS_MIGRATED = "已迁移"

# result_store 里承载 v1 文本行数的字段
V1_RAW_FIELD = "原始行数"
V1_VALID_FIELD = "有效行数"
BATCH_FIELD = "批次标识"
DAY_FIELD = "采集时间"


def parse_integer_text(value: Any) -> int | None:
    """把 v1 文本字段解析成整数。

    只有“完整的十进制整数文本”才算可迁移的数字（允许两侧空白与显式正号）；
    空串、浮点数文本、带单位文本或任何占位文本都返回 ``None``，
    由调用方保留原文并标记待核查，绝不静默折算成 0。
    """
    if value is None:
        return None
    if isinstance(value, bool):  # bool 是 int 的子类，这里明确排除
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    body = text[1:] if text[:1] in "+-" else text
    if not body.isdigit():
        return None
    try:
        return int(text)
    except ValueError:  # 理论上不会到这里，留作防御
        return None


def decimal_text(value: Any) -> str:
    """把统计值统一转成十进制字符串，保留完整数位（不经过 float）。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    text = str(value).strip()
    parsed = parse_integer_text(text)
    return str(parsed) if parsed is not None else text


class CollectionStatsService(BaseModuleService):
    """版本化采集统计领域服务。"""

    MODULE = "collection_stats"
    ENTITY = "采集统计"
    OPERATION_TERM = "迁移"
    LIST_FIELDS = [
        "批次标识", "采集时间", "统计版本", "原始行数", "有效行数",
        "迁移状态", "待核查原因", "迁移批次", "来源原文",
    ]
    REQUIRED_FIELDS = ["批次标识", "采集时间", "原始行数", "有效行数"]
    STATUS_ORDER = [STATUS_PENDING_REVIEW, STATUS_MIGRATED]
    ACTION_RULES = {"重新迁移": STATUS_PENDING_REVIEW}
    NEGATIVE_ACTIONS = []

    # ------------------------------------------------------------------
    # 基础读写
    # ------------------------------------------------------------------
    def _table(self) -> list[dict[str, Any]]:
        return store.rows(self.MODULE)

    def find_by_batch(self, batch_id: str) -> dict[str, Any] | None:
        for row in self._table():
            if row.get(BATCH_FIELD) == batch_id:
                return row
        return None

    def upsert_v2_stat(
        self,
        batch_id: str,
        day: str,
        raw_count: int,
        valid_count: int,
        *,
        status: str = STATUS_MIGRATED,
        review_reason: str = "",
        migration_run: str = "",
        source_snapshot: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """按批次标识写入/更新一条 v2 统计（同批次只有一条）。"""
        existing = self.find_by_batch(batch_id)
        snapshot = source_snapshot or {}
        if existing is None:
            entry = {
                "id": max((int(r.get("id", 0)) for r in self._table()), default=0) + 1,
                BATCH_FIELD: batch_id,
                DAY_FIELD: day,
                "统计版本": STAT_VERSION_V2,
                "原始行数": int(raw_count),
                "有效行数": int(valid_count),
                "迁移状态": status,
                "待核查原因": review_reason,
                "迁移批次": migration_run,
                "来源原文": snapshot,
                "status": status,
                "pending": status == STATUS_PENDING_REVIEW,
                "abnormal": status == STATUS_PENDING_REVIEW,
            }
            self._table().append(entry)
            return entry
        # 已迁移记录做幂等更新时不改变其确认状态，除非显式回退为待核查。
        existing[DAY_FIELD] = day
        existing["统计版本"] = STAT_VERSION_V2
        existing["原始行数"] = int(raw_count)
        existing["有效行数"] = int(valid_count)
        if status == STATUS_PENDING_REVIEW or existing["迁移状态"] != STATUS_MIGRATED:
            existing["迁移状态"] = status
        existing["待核查原因"] = review_reason or existing.get("待核查原因", "")
        if migration_run:
            existing["迁移批次"] = migration_run
        if snapshot:
            existing["来源原文"] = snapshot
        existing["status"] = existing["迁移状态"]
        existing["pending"] = existing["迁移状态"] == STATUS_PENDING_REVIEW
        existing["abnormal"] = existing["迁移状态"] == STATUS_PENDING_REVIEW
        return existing

    def record_manual_stat(
        self,
        batch_id: str,
        day: str,
        raw_count: int,
        valid_count: int,
        *,
        migration_run: str = "manual",
        source_snapshot: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """新版采集直接登记一条已迁移的 v2 统计。"""
        return self.upsert_v2_stat(
            batch_id, day, raw_count, valid_count,
            status=STATUS_MIGRATED, migration_run=migration_run,
            source_snapshot=source_snapshot,
        )

    # ------------------------------------------------------------------
    # 历史迁移
    # ------------------------------------------------------------------
    def migrate_legacy_batches(self, migration_run: str) -> dict[str, Any]:
        """把 ``result_store`` 中的旧批迁移到 v2 整数口径。

        幂等规则：

        * 已是「已迁移」的批次直接跳过，续做迁移不会重复计数；
        * 「待核查」批次每次都会重新尝试解析当前 v1 文本，
          占位文本被人工修正后再次迁移即可转为「已迁移」；
        * 解析失败的批次保留 v1 原文快照并标记待核查，不写 0。
        """
        legacy_rows = store.rows("result_store")
        migrated: list[str] = []
        skipped: list[str] = []
        pending_review: list[dict[str, str]] = []

        for legacy in legacy_rows:
            batch_id = str(legacy.get(BATCH_FIELD, ""))
            day = str(legacy.get(DAY_FIELD, ""))
            raw_text = legacy.get(V1_RAW_FIELD)
            valid_text = legacy.get(V1_VALID_FIELD)
            snapshot = {
                V1_RAW_FIELD: "" if raw_text is None else str(raw_text),
                V1_VALID_FIELD: "" if valid_text is None else str(valid_text),
            }
            existing = self.find_by_batch(batch_id)
            if existing is not None and existing.get("迁移状态") == STATUS_MIGRATED:
                skipped.append(batch_id)
                continue

            raw_count = parse_integer_text(raw_text)
            valid_count = parse_integer_text(valid_text)
            bad_fields = [
                name for name, parsed in (
                    (V1_RAW_FIELD, raw_count), (V1_VALID_FIELD, valid_count),
                ) if parsed is None
            ]
            if bad_fields:
                reason = "占位文本无法解析为整数：" + "、".join(bad_fields)
                entry = self.upsert_v2_stat(
                    batch_id, day, 0, 0,
                    status=STATUS_PENDING_REVIEW,
                    review_reason=reason,
                    migration_run=migration_run,
                    source_snapshot=snapshot,
                )
                # 0 仅作占位存储，标记后任何统计都不得把它当作真实零值。
                entry["原始行数"] = None
                entry["有效行数"] = None
                pending_review.append({"批次标识": batch_id, "待核查原因": reason})
                continue

            self.upsert_v2_stat(
                batch_id, day, raw_count, valid_count,
                status=STATUS_MIGRATED,
                migration_run=migration_run,
                source_snapshot=snapshot,
            )
            migrated.append(batch_id)

        return {
            "migration_run": migration_run,
            "total_legacy": len(legacy_rows),
            "migrated": migrated,
            "skipped_migrated": skipped,
            "pending_review": pending_review,
            "migrated_count": len(migrated),
            "pending_count": len(pending_review),
        }

    # ------------------------------------------------------------------
    # 查询 / 对账
    # ------------------------------------------------------------------
    def _in_range(self, day: str, start: str | None, end: str | None) -> bool:
        if start and day < start:
            return False
        if end and day > end:
            return False
        return True

    def v2_daily_reconcile(
        self, start: str | None = None, end: str | None = None,
    ) -> dict[str, Any]:
        """v2 整数口径对账：给出同日范围的原始/有效总行数与迁移完整度。"""
        rows = [
            row for row in self._table()
            if self._in_range(str(row.get(DAY_FIELD, "")), start, end)
        ]
        migrated_rows = [r for r in rows if r.get("迁移状态") == STATUS_MIGRATED]
        pending_rows = [r for r in rows if r.get("迁移状态") == STATUS_PENDING_REVIEW]

        raw_total = sum(int(r["原始行数"]) for r in migrated_rows if r.get("原始行数") is not None)
        valid_total = sum(int(r["有效行数"]) for r in migrated_rows if r.get("有效行数") is not None)

        # 已确认迁移覆盖到的日期范围，定时对账据此判断“确切可用范围”。
        migrated_days = sorted({str(r.get(DAY_FIELD, "")) for r in migrated_rows})
        available_range = {
            "start": migrated_days[0] if migrated_days else "",
            "end": migrated_days[-1] if migrated_days else "",
        }
        pending_days = sorted({str(r.get(DAY_FIELD, "")) for r in pending_rows})

        return {
            "统计版本": STAT_VERSION_V2,
            "开始日期": start or "",
            "结束日期": end or "",
            "迁移完整": not pending_rows,
            "原始总行数": decimal_text(raw_total),
            "有效总行数": decimal_text(valid_total),
            "已迁移批次数": len(migrated_rows),
            "待核查批次数": len(pending_rows),
            "可用范围": available_range,
            "待核查日期": pending_days,
            "待核查批次": [
                {
                    "批次标识": r.get(BATCH_FIELD, ""),
                    "采集时间": r.get(DAY_FIELD, ""),
                    "待核查原因": r.get("待核查原因", ""),
                    "来源原文": r.get("来源原文", {}),
                }
                for r in pending_rows
            ],
        }

    def v1_daily_reconcile(
        self, start: str | None = None, end: str | None = None,
    ) -> dict[str, Any]:
        """v1 文本口径对账：保持旧批回执的文本行数语义。

        可解析的文本参与求和（结果仍以十进制字符串输出），不可解析的
        占位文本不计入合计、原样列入 ``文本行批次``，与旧 CSV 看到的内容一致。
        """
        legacy_rows = [
            row for row in store.rows("result_store")
            if self._in_range(str(row.get(DAY_FIELD, "")), start, end)
        ]
        raw_total = 0
        valid_total = 0
        text_only: list[dict[str, str]] = []
        for row in legacy_rows:
            raw_text = row.get(V1_RAW_FIELD)
            valid_text = row.get(V1_VALID_FIELD)
            raw_parsed = parse_integer_text(raw_text)
            valid_parsed = parse_integer_text(valid_text)
            if raw_parsed is None or valid_parsed is None:
                text_only.append({
                    "批次标识": str(row.get(BATCH_FIELD, "")),
                    V1_RAW_FIELD: "" if raw_text is None else str(raw_text),
                    V1_VALID_FIELD: "" if valid_text is None else str(valid_text),
                })
                continue
            raw_total += raw_parsed
            valid_total += valid_parsed
        return {
            "统计版本": STAT_VERSION_V1,
            "开始日期": start or "",
            "结束日期": end or "",
            "原始总行数": decimal_text(raw_total),
            "有效总行数": decimal_text(valid_total),
            "批次数": len(legacy_rows),
            "文本行批次": text_only,
        }

    def reconcile(
        self,
        version: str = STAT_VERSION_V2,
        start: str | None = None,
        end: str | None = None,
    ) -> dict[str, Any]:
        if version == STAT_VERSION_V1:
            return self.v1_daily_reconcile(start, end)
        return self.v2_daily_reconcile(start, end)

    # ------------------------------------------------------------------
    # 对外序列化：整数字段转十进制字符串，保留完整数位
    # ------------------------------------------------------------------
    def to_wire(self, row: dict[str, Any]) -> dict[str, Any]:
        import json
        wire = dict(row)
        for field in (V1_RAW_FIELD, V1_VALID_FIELD):
            if wire.get(field) is None:
                wire[field] = ""
            else:
                wire[field] = decimal_text(wire[field])
        # 来源原文是 dict，CSV 写出时需要稳定的文本表示，统一 JSON 编码
        if isinstance(wire.get("来源原文"), (dict, list)):
            wire["来源原文"] = json.dumps(wire["来源原文"], ensure_ascii=False)
        return wire

    def list_entries(self, **kwargs: Any) -> tuple[list[dict[str, Any]], int]:
        items, total = super().list_entries(**kwargs)
        return [self.to_wire(row) for row in items], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = super().get_entry(entry_id)
        return self.to_wire(row) if row is not None else None
