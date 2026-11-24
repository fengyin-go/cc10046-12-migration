"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。

版本约定：``result_store`` 本身保持 v1 文本口径（列表与 CSV 原样返回
``原始行数`` / ``有效行数`` 文本，供依赖 v1 的定时对账使用）；新版整数口径
写入独立的 ``collection_stats``（v2），由 :class:`CollectionStatsService` 管理。
"""
from __future__ import annotations

from app.services.base import BaseModuleService


class ResultStoreService(BaseModuleService):
    MODULE = "result_store"
    ENTITY = "存储批次"
    OPERATION_TERM = "采集"
    LIST_FIELDS = ["批次标识", "来源配置", "采集时间", "原始行数", "有效行数", "存储路径", "压缩格式", "校验摘要"]
    REQUIRED_FIELDS = ["批次标识", "来源配置", "采集时间"]
    STATUS_ORDER = ["存储中", "已落库", "校验未过", "已归档"]
    ACTION_RULES = {"执行校验": "已落库", "发起归档": "已落库"}
    NEGATIVE_ACTIONS = []

    def domain_store_result(self, batch_id: str, source_config: str,
                            row_count: int, valid_count: int) -> dict:
        """结构化存储采集结果。

        v1 表保留文本行数字段；同时登记一条 v2 整数统计，保证新版对账
        与导出通道可以直接取到整数口径，且大整数不经过浮点。
        """
        from app.store import store
        from app.services.collection_stats import CollectionStatsService
        import time
        rows = store.rows(self.MODULE)
        store_path = f"/data/results/{batch_id}.json.gz"
        today = time.strftime("%Y-%m-%d")
        raw_count = int(row_count)
        good_count = int(valid_count)
        entry = {
            "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
            "批次标识": batch_id,
            "来源配置": source_config,
            "采集时间": today,
            # v1 口径：文本行数，旧列表 / CSV / 回执继续按原格式读取
            "原始行数": str(raw_count),
            "有效行数": str(good_count),
            "存储路径": store_path,
            "压缩格式": "gzip",
            "校验摘要": f"md5:{batch_id[:8]}",
            "status": "已落库",
            "pending": False,
            "abnormal": False,
        }
        rows.append(entry)
        # v2 口径：任意精度整数，独立表承载，不影响 v1 原始文本
        CollectionStatsService().record_manual_stat(
            batch_id, today, raw_count, good_count,
            migration_run="live-v2",
            source_snapshot={"原始行数": str(raw_count), "有效行数": str(good_count)},
        )
        return entry
