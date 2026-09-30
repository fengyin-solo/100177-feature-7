"""压力容器业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services.compliance import CLOSED_STATUSES, compliance_service
from app.store import store

MODULE = "vessel"
REQUIRED_FIELDS = ["容器编号", "容器名称", "设计压力"]
OPTIONAL_FIELDS = ["容积规格", "介质类别", "使用场所", "下次检验日"]
STATUS_ORDER = ["待投用", "在用运行", "停用待检", "已报废"]
ACTION_RULES = {"办理投用": "在用运行", "安排检验": "停用待检", "报废容器": "已报废"}
NEGATIVE_ACTIONS = []

# 合规口径：封闭态（停用待检/已报废）容器只能查看，任何状态迁出动作都拦下；
# 其中"办理投用"属于明确的回退到在用状态。
BACK_TO_IN_USE_ACTION = "办理投用"


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
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        view = compliance_service.build_view()
        for item in view["groups"]:
            for row in item["items"]:
                if int(row["id"]) == entry_id:
                    return row
        return None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        # 容积规格等补充字段按提交值落库；不提交就留空，由合规视图归入待补全。
        for field in OPTIONAL_FIELDS:
            if values.get(field) not in (None, ""):
                entry[field] = values.get(field)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        compliance_service.on_vessel_created(entry)
        return entry, []

    def update_specs(
        self, entry_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        """补全设计压力/容积规格。封闭态容器只读，不允许在这里改动或恢复在用。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"压力容器 {entry_id} 不存在或已归档"
        if entry.get("status") in CLOSED_STATUSES:
            return None, f"容器当前为「{entry.get('status')}」封闭状态，只允许查看，不能补录资料"
        fields = ["设计压力", "容积规格"]
        updated = [field for field in fields if str(values.get(field) or "").strip()]
        if not updated:
            return None, "请至少补全设计压力或容积规格中的一项"
        for field in fields:
            if str(values.get(field) or "").strip():
                entry[field] = str(values.get(field)).strip()
        compliance_service.on_specs_updated(entry)
        return entry, f"资料已补全（{'、'.join(updated)}），合规判定已更新"

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"压力容器 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于压力容器可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        if action == BACK_TO_IN_USE_ACTION and entry.get("status") in CLOSED_STATUSES:
            return None, (
                f"容器当前为「{entry.get('status')}」封闭状态，只能查看，"
                "不允许在合规判定里回退到在用状态"
            )
        if entry.get("status") in CLOSED_STATUSES:
            return None, f"容器当前为「{entry.get('status')}」封闭状态，只能查看，不能变更状态"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        compliance_service.on_status_changed(entry, target)
        return entry, f"压力容器已{action}"
