"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import resource
from app.routers import chains
from app.routers import stats

# stats 含固定前缀 /api/collection-stats，需放在通用 /api/{module} 之前注册
ROUTERS = [stats, resource, chains]
