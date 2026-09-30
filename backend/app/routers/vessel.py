"""压力容器接口：维护压力容器，覆盖办理投用、安排检验、报废容器等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.compliance import compliance_service
from app.services.vessel import VesselService

router = APIRouter(prefix="/api/vessel", tags=["压力容器"])

service = VesselService()

LIST_FIELDS = ["容器编号", "容器名称", "设计压力", "容积规格", "介质类别", "使用场所", "下次检验日", "容器状态"]
STATUSES = ["待投用", "在用运行", "停用待检", "已报废"]


class RangePayload(BaseModel):
    """允许范围调整；adjust_key 用于同一次调整重复下发时的幂等去重。"""

    pressure_min: float
    pressure_max: float
    volume_min: float
    volume_max: float
    adjust_key: str | None = None
    remark: str | None = None


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


@router.get("/compliance/view")
def compliance_view(
    keyword: str | None = Query(default=None, description="按容器编号检索"),
    status: str | None = Query(default=None, description="按容器状态过滤"),
) -> dict[str, Any]:
    """合规视图：按状态分组、超范围排前，缺失设计压力/容积规格的进入待补全栏。

    首次访问会为没有判定结果的存量容器按登记当时的范围回填判定记录。
    """
    return compliance_service.build_view(keyword=keyword, status=status)


@router.get("/compliance/range")
def range_history() -> dict[str, Any]:
    """读取当前允许范围与历次版本（历史判定始终挂在判定当时的版本上）。"""
    return {
        "current": compliance_service.current_range(),
        "history": compliance_service.range_history(),
    }


@router.put("/compliance/range", response_model=ActionResult)
def adjust_range(payload: RangePayload) -> ActionResult:
    """调整允许范围：生效后按新版本重判；同一 adjust_key 重复下发只生效一次。"""
    rng, changed, message = compliance_service.adjust_range(
        pressure_min=payload.pressure_min,
        pressure_max=payload.pressure_max,
        volume_min=payload.volume_min,
        volume_max=payload.volume_max,
        adjust_key=payload.adjust_key,
        remark=payload.remark,
    )
    if not changed and "一致" in message:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=rng)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条压力容器明细（含当前合规判定）；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"压力容器 {entry_id} 不存在或已归档")
    return entry


@router.get("/{entry_id}/compliance/records")
def compliance_records(entry_id: int) -> dict[str, Any]:
    """读取单台容器的全部判定记录（含已归档的历史结论）。"""
    if service.get_entry(entry_id) is None:
        raise HTTPException(status_code=404, detail=f"压力容器 {entry_id} 不存在或已归档")
    records = compliance_service.records_of(entry_id)
    return {"entry_id": entry_id, "total": len(records), "items": records}


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


@router.put("/{entry_id}/specs", response_model=ActionResult)
def update_specs(entry_id: int, payload: EntryPayload) -> ActionResult:
    """补全设计压力/容积规格，提交后按当前范围重新判定；封闭态容器只允许查看。"""
    entry, message = service.update_specs(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出压力容器清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "vessel", "total": total, "items": items}
