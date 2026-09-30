"""压力容器业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.services.vessel_compliance import (
    IN_SERVICE_STATUS,
    LOCKED_STATUSES,
    compliance_service,
)
from app.store import store

MODULE = "vessel"
REQUIRED_FIELDS = ["容器编号", "容器名称", "设计压力"]
OPTIONAL_FIELDS = ["容积规格", "介质类别", "使用场所", "下次检验日", "容器状态"]
STATUS_ORDER = ["待投用", "在用运行", "停用待检", "已报废"]
ACTION_RULES = {"办理投用": "在用运行", "安排检验": "停用待检", "报废容器": "已报废"}
NEGATIVE_ACTIONS = []


class VesselService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("容器编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in OPTIONAL_FIELDS:
            value = values.get(field)
            if value is not None and str(value).strip():
                entry[field] = value
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        # 登记日用于确定当时生效的范围；既有容器数据不含该字段，不做回填改写。
        entry["registered_at"] = str(values.get("registered_at") or date.today().isoformat())
        rows.append(entry)
        # 新登记容器当场按当前范围出一条判定（规格缺失则落「待补全」）。
        compliance_service.rejudge_vessel(entry, source="登记判定")
        return entry, []

    def run_action(
        self,
        entry_id: int,
        action: str,
        *,
        channel: str = "default",
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"压力容器 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于压力容器可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        # 合规判定视图只读规则：已停用或维护封闭（报废）的容器只能查看，
        # 不允许借合规通道回退到在用状态。
        if channel == "compliance" and entry.get("status") in LOCKED_STATUSES \
                and target == IN_SERVICE_STATUS:
            return None, "已停用或维护封闭的容器只能查看，不能在合规判定里回退到在用状态"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        compliance_service.rejudge_vessel(entry, source="状态切换")
        return entry, f"压力容器已{action}"
