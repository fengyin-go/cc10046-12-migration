"""版本化统计路由的 HTTP 层测试：路由优先级、CSV 兼容与大整数序列化。"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services.result_store import ResultStoreService  # noqa: E402
from app.store import store  # noqa: E402

DAY = "2026-11-24"
client = TestClient(app)


def _reset() -> None:
    store._tables["result_store"] = [
        {"id": 1, "批次标识": "B-A", "来源配置": "cfg", "采集时间": DAY,
         "原始行数": "120", "有效行数": "100", "存储路径": "/x",
         "压缩格式": "gzip", "校验摘要": "m", "status": "已落库",
         "pending": False, "abnormal": False},
        {"id": 2, "批次标识": "B-B", "来源配置": "cfg", "采集时间": DAY,
         "原始行数": "80", "有效行数": "70", "存储路径": "/x",
         "压缩格式": "gzip", "校验摘要": "m", "status": "已落库",
         "pending": False, "abnormal": False},
        {"id": 3, "批次标识": "B-C", "来源配置": "cfg", "采集时间": DAY,
         "原始行数": "占位-未知", "有效行数": "占位-未知", "存储路径": "/x",
         "压缩格式": "gzip", "校验摘要": "m", "status": "校验未过",
         "pending": False, "abnormal": False},
    ]
    store._tables["collection_stats"] = []
    store._tables["export_channel"] = []


def test_fixed_stats_routes_take_precedence() -> None:
    _reset()
    resp = client.post("/api/collection-stats/migrations", json={"migration_run": "r1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["migrated_count"] == 2
    assert body["pending_count"] == 1


def test_reconcile_endpoint_partial_then_complete() -> None:
    _reset()
    client.post("/api/collection-stats/migrations", json={"migration_run": "r1"})
    partial = client.get(
        "/api/collection-stats/reconcile",
        params={"version": "v2", "start": DAY, "end": DAY},
    ).json()
    assert partial["迁移完整"] is False
    assert partial["原始总行数"] == "200"
    assert partial["可用范围"] == {"start": DAY, "end": DAY}
    assert partial["待核查批次"][0]["来源原文"]["原始行数"] == "占位-未知"

    v1 = client.get(
        "/api/collection-stats/reconcile",
        params={"version": "v1", "start": DAY, "end": DAY},
    ).json()
    assert v1["统计版本"] == "v1"
    assert v1["原始总行数"] == "200"

    for row in store.rows("result_store"):
        if row["批次标识"] == "B-C":
            row["原始行数"], row["有效行数"] = "50", "40"
    client.post("/api/collection-stats/migrations", json={"migration_run": "r1"})
    full = client.get(
        "/api/collection-stats/reconcile",
        params={"version": "v2", "start": DAY, "end": DAY},
    ).json()
    assert full["迁移完整"] is True
    assert full["原始总行数"] == "250"
    assert full["有效总行数"] == "210"


def test_v1_list_and_csv_keep_original_text() -> None:
    _reset()
    items = client.get("/api/result_store").json()["items"]
    placeholder = [x for x in items if x["批次标识"] == "B-C"][0]
    assert placeholder["原始行数"] == "占位-未知"

    csv_text = client.get("/api/result_store/export").text
    lines = [ln for ln in csv_text.lstrip("﻿").splitlines() if ln]
    assert lines[0] == (
        "id,批次标识,来源配置,采集时间,原始行数,有效行数,存储路径,压缩格式,校验摘要,status"
    )
    assert any("占位-未知" in ln for ln in lines)


def test_big_integer_serialized_as_full_decimal_string() -> None:
    _reset()
    ResultStoreService().domain_store_result("B-BIG", "cfg", 9007199254740993, 0)
    raw = client.get("/api/collection-stats/batches/B-BIG").content.decode()
    assert "9007199254740993" in raw
    assert "9007199254740992" not in raw  # 没有被 JS 安全整数截断

    body = client.get("/api/collection-stats/batches/B-BIG").json()
    assert body["原始行数"] == "9007199254740993"
    assert body["有效行数"] == "0"

    stats_csv = client.get("/api/collection_stats/export").text
    assert "9007199254740993" in stats_csv


def test_correction_then_realign_and_versioned_export() -> None:
    _reset()
    client.post("/api/collection-stats/migrations", json={"migration_run": "r1"})

    fixed = client.post(
        "/api/collection-stats/batches/B-C/corrections",
        json={"原始行数": "50", "有效行数": "40"},
    ).json()
    assert fixed["ok"] is True
    assert fixed["统计"]["迁移状态"] == "已迁移"
    assert fixed["统计"]["原始行数"] == "50"

    full = client.get(
        "/api/collection-stats/reconcile",
        params={"version": "v2", "start": DAY, "end": DAY},
    ).json()
    assert full["原始总行数"] == "250"
    assert full["有效总行数"] == "210"

    v2_export = client.post(
        "/api/collection-stats/batches/B-A/exports",
        json={"export_format": "csv", "stat_version": "v2"},
    ).json()
    assert v2_export["统计版本"] == "v2"
    assert v2_export["原始总行数"] == "250"
    assert v2_export["对账状态"] == "当日迁移完整"

    v1_export = client.post(
        "/api/collection-stats/batches/B-A/exports",
        json={"export_format": "csv", "stat_version": "v1"},
    ).json()
    assert v1_export["统计版本"] == "v1"
    assert v1_export["文本原始行数"] == "120"
    assert v1_export["原始总行数"] == "120"


def test_correction_unknown_batch_returns_404() -> None:
    _reset()
    resp = client.post(
        "/api/collection-stats/batches/NO-SUCH/corrections",
        json={"原始行数": "1", "有效行数": "1"},
    )
    assert resp.status_code == 404
