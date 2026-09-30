"""压力容器接口：维护压力容器，覆盖办理投用、安排检验、报废容器等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.vessel import VesselService
from app.services.vessel_compliance import compliance_service

router = APIRouter(prefix="/api/vessel", tags=["压力容器"])

service = VesselService()

LIST_FIELDS = ["容器编号", "容器名称", "设计压力", "容积规格", "介质类别", "使用场所", "下次检验日", "容器状态"]
STATUSES = ["待投用", "在用运行", "停用待检", "已报废"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按容器编号检索"),
    status: str | None = Query(default=None, description="待投用、在用运行、停用待检、已报废"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按容器编号与状态过滤压力容器列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/compliance")
def compliance_view(
    keyword: str | None = Query(default=None, description="按容器编号检索"),
    status: str | None = Query(default=None, description="按容器状态过滤"),
) -> dict[str, Any]:
    """合规视图：按容器状态分组，超范围标记并置顶，缺规格归入待补全。

    判定结论来自每台容器的最新判定记录，刷新或从详情返回时口径不变。
    """
    return compliance_service.latest_view(keyword=keyword, status=status)


@router.get("/ranges")
def list_ranges() -> dict[str, Any]:
    """允许范围版本史：历史判定仍按各版本当时的范围保留。"""
    versions = compliance_service.list_ranges()
    return {"current": compliance_service.current_range(), "versions": versions}


@router.post("/ranges/adjust", response_model=ActionResult)
def adjust_range(payload: EntryPayload) -> ActionResult:
    """下发新的允许范围并按新范围重判；同一次调整（token）或同范围重复下发只生效一次。"""
    token = payload.values.get("token") if payload.values else None
    range_row, message, applied = compliance_service.adjust_range(
        payload.values, token=str(token) if token is not None else None
    )
    if range_row is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=applied, message=message, entry=range_row)


@router.get("/{entry_id}/judgments")
def list_judgments(entry_id: int) -> dict[str, Any]:
    """单台容器的判定史：只追加，范围调整不改动历史结论。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"压力容器 {entry_id} 不存在或已归档")
    items = compliance_service.list_judgments(entry_id)
    return {"vesselId": entry_id, "total": len(items), "items": items}


@router.post("/{entry_id}/compliance-actions", response_model=ActionResult)
def run_compliance_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """合规判定视图内的状态切换：停用/报废容器只读，不能回退到在用；切换后标记随之更新。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action, channel="compliance")
    if entry is None:
        return ActionResult(ok=False, message=message)
    judgment = compliance_service.latest_map().get(entry_id)
    return ActionResult(ok=True, message=message, entry={"vessel": entry, "compliance": judgment})


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条压力容器明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"压力容器 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条压力容器，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="压力容器已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条压力容器执行办理投用、安排检验、报废容器；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出压力容器清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "vessel", "total": total, "items": items}
