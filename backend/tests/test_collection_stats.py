"""版本化采集统计（v2 整数口径）迁移与对账测试。

覆盖验收场景：
- 同日两批可迁移行数 + 一批旧占位文本；
- 部署期间 v2 对账能看到确切可用范围或“等待完整迁移”；
- 修正占位文本后续做迁移，只计一次，并对齐当日完整合计；
- v1 列表 / CSV 保持原文本格式，旧 CSV 按原值回查；
- 9007199254740993 与真实零的转换不丢数位、不把占位当零。
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.collection_stats import (  # noqa: E402
    CollectionStatsService,
    STATUS_MIGRATED,
    STATUS_PENDING_REVIEW,
    parse_integer_text,
    decimal_text,
)
from app.services.export_channel import ExportChannelService  # noqa: E402
from app.services.result_store import ResultStoreService  # noqa: E402
from app.store import store  # noqa: E402

DAY = "2026-11-24"
BIG_INT = 9007199254740993  # 超过 JS Number.MAX_SAFE_INTEGER


def _reset_store() -> None:
    store._tables["result_store"] = [
        {"id": 1, "批次标识": "B-A", "来源配置": "cfg", "采集时间": DAY,
         "原始行数": "120", "有效行数": "100", "status": "已落库"},
        {"id": 2, "批次标识": "B-B", "来源配置": "cfg", "采集时间": DAY,
         "原始行数": "80", "有效行数": "70", "status": "已落库"},
        {"id": 3, "批次标识": "B-C", "来源配置": "cfg", "采集时间": DAY,
         "原始行数": "结果存储样例3", "有效行数": "结果存储样例3",
         "status": "校验未过"},
    ]
    store._tables["collection_stats"] = []
    store._tables["export_channel"] = []


def test_parse_integer_text_rejects_placeholder() -> None:
    assert parse_integer_text("结果存储样例1") is None
    assert parse_integer_text("") is None
    assert parse_integer_text("12.5") is None
    assert parse_integer_text("1,000") is None
    assert parse_integer_text("  +7 ") == 7
    assert parse_integer_text(0) == 0
    assert parse_integer_text(BIG_INT) == BIG_INT
    assert decimal_text(BIG_INT) == "9007199254740993"


def test_migration_partial_day_then_complete() -> None:
    _reset_store()
    svc = CollectionStatsService()

    first = svc.migrate_legacy_batches("run-1")
    assert first["migrated"] == ["B-A", "B-B"]
    assert first["migrated_count"] == 2
    assert first["pending_count"] == 1
    assert first["pending_review"][0]["批次标识"] == "B-C"

    # 部署期间：当日仍在等待完整迁移，但能给出确切的已迁移可用范围
    rec = svc.v2_daily_reconcile(DAY, DAY)
    assert rec["迁移完整"] is False
    assert rec["原始总行数"] == "200"
    assert rec["有效总行数"] == "170"
    assert rec["可用范围"] == {"start": DAY, "end": DAY}
    assert rec["待核查日期"] == [DAY]
    pending = rec["待核查批次"][0]
    assert pending["来源原文"]["原始行数"] == "结果存储样例3"

    # 占位文本不能被当成零：待核查行整数字段为空而不是 0
    wire = svc.to_wire(svc.find_by_batch("B-C"))
    assert wire["迁移状态"] == STATUS_PENDING_REVIEW
    assert wire["原始行数"] == "" and wire["有效行数"] == ""


def test_migration_is_idempotent_for_confirmed_batches() -> None:
    _reset_store()
    svc = CollectionStatsService()
    svc.migrate_legacy_batches("run-1")
    again = svc.migrate_legacy_batches("run-2")
    # 已确认迁移的批次续做时只计一次
    assert again["migrated_count"] == 0
    assert set(again["skipped_migrated"]) == {"B-A", "B-B"}
    # 待核查的 B-C 仍会被重新尝试
    assert again["pending_count"] == 1
    assert len(svc._table()) == 3


def test_fix_placeholder_then_realign_daily_total() -> None:
    _reset_store()
    svc = CollectionStatsService()
    svc.migrate_legacy_batches("run-1")

    for row in store.rows("result_store"):
        if row["批次标识"] == "B-C":
            row["原始行数"] = "50"
            row["有效行数"] = "40"

    rerun = svc.migrate_legacy_batches("run-1")  # 同一次迁移重跑
    assert rerun["migrated"] == ["B-C"]
    assert rerun["pending_count"] == 0

    rec = svc.v2_daily_reconcile(DAY, DAY)
    assert rec["迁移完整"] is True
    # 120 + 80 + 50 = 250；100 + 70 + 40 = 210
    assert rec["原始总行数"] == "250"
    assert rec["有效总行数"] == "210"
    assert svc.find_by_batch("B-C")["迁移状态"] == STATUS_MIGRATED


def test_v1_reconcile_and_legacy_csv_keep_text() -> None:
    _reset_store()
    svc = CollectionStatsService()
    svc.migrate_legacy_batches("run-1")

    v1 = svc.v1_daily_reconcile(DAY, DAY)
    assert v1["统计版本"] == "v1"
    assert v1["原始总行数"] == "200"
    # 占位批原样保留在文本行批次里，供定时对账回查
    text_batch = v1["文本行批次"][0]
    assert text_batch["批次标识"] == "B-C"
    assert text_batch["原始行数"] == "结果存储样例3"

    # result_store 的 v1 文本字段没有被迁移改动
    legacy = {r["批次标识"]: r for r in store.rows("result_store")}
    assert legacy["B-A"]["原始行数"] == "120"
    assert legacy["B-C"]["原始行数"] == "结果存储样例3"

    # 修正后重跑，旧 CSV 仍按当前原值回查
    for row in store.rows("result_store"):
        if row["批次标识"] == "B-C":
            row["原始行数"], row["有效行数"] = "50", "40"
    svc.migrate_legacy_batches("run-2")
    legacy2 = {r["批次标识"]: r for r in store.rows("result_store")}
    assert legacy2["B-C"]["原始行数"] == "50"
    assert legacy2["B-A"]["原始行数"] == "120"


def test_big_integer_and_real_zero_keep_full_digits() -> None:
    _reset_store()
    ResultStoreService().domain_store_result("B-BIG", "cfg", BIG_INT, 0)
    svc = CollectionStatsService()
    row = svc.find_by_batch("B-BIG")
    assert row is not None
    assert row["原始行数"] == BIG_INT
    assert row["有效行数"] == 0  # 真实零就是零
    wire = svc.to_wire(row)
    assert wire["原始行数"] == "9007199254740993"
    assert wire["有效行数"] == "0"

    # v1 文本同样保留完整数位
    legacy = [r for r in store.rows("result_store") if r["批次标识"] == "B-BIG"][0]
    assert legacy["原始行数"] == "9007199254740993"


def test_export_channel_uses_versioned_stats() -> None:
    _reset_store()
    svc = CollectionStatsService()
    svc.migrate_legacy_batches("run-1")
    export_svc = ExportChannelService()

    # 待核查日：v2 导出提示等待完整迁移，已迁移批次合计仍可用
    pending_export = export_svc.build_export_stats("B-A", "v2")
    assert pending_export["统计版本"] == "v2"
    assert pending_export["对账状态"] == "当日仍在等待完整迁移"
    assert pending_export["批次原始行数"] == "120"
    assert pending_export["原始总行数"] == "200"

    # v1 回执仍是文本行数
    v1_export = export_svc.build_export_stats("B-C", "v1")
    assert v1_export["文本原始行数"] == "结果存储样例3"
    assert v1_export["原始总行数"] == "结果存储样例3"

    # 修正后当日完整
    for row in store.rows("result_store"):
        if row["批次标识"] == "B-C":
            row["原始行数"], row["有效行数"] = "50", "40"
    svc.migrate_legacy_batches("run-2")
    done = export_svc.domain_export("B-A", "csv", stat_version="v2")
    assert done["对账状态"] == "当日迁移完整"
    assert done["原始总行数"] == "250"
    assert done["有效总行数"] == "210"


def test_export_big_integer_preserves_decimal_digits() -> None:
    _reset_store()
    ResultStoreService().domain_store_result("B-BIG", "cfg", BIG_INT, 0)
    entry = ExportChannelService().domain_export("B-BIG", "csv", stat_version="v2")
    assert entry["批次原始行数"] == "9007199254740993"
    assert entry["批次有效行数"] == "0"
    assert str(BIG_INT + 1) not in entry["批次原始行数"]
