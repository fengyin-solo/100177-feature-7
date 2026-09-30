"""压力容器合规判定：允许范围版本、判定记录、存量回填与范围重判。

口径约定（与产品侧的合规视图一一对应）：

- 范围以"版本"为单位只增不改：只有真正发生变化的调整才生成新版本，
  历史判定永远挂在判定当时的范围版本上（range_snapshot 随记录冻结）。
- 判定记录只追加、不改写：已报废归档的记录冻结；存量回填与范围重判都
  不会覆盖或推翻已归档的历史结论。
- 同一次调整靠幂等键（adjust_key）去重：重复下发直接返回同一版本，
  不生成新版本、不再触发重判；边界与当前版本相同也视为重复。
- 已停用（停用待检）/已报废（已报废）容器处于封闭态：不补录资料、
  不随范围调整重判，业务侧也不允许从封闭态回退到在用状态。
- 设计压力或容积规格缺失（为空或无法识别为数值）归入"待补全"，
  不参与范围比较，范围调整也不会改变其判定。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.store import store

RANGE_TABLE = "vessel_range_version"
RECORD_TABLE = "vessel_compliance_record"
VESSEL_MODULE = "vessel"

# 初始允许范围：早于存量容器的登记日期（2026-09-01 起），
# 因此存量回填时取到的就是这一版。
INITIAL_RANGE: dict[str, Any] = {
    "revision": 1,
    "pressure_min": 0.1,
    "pressure_max": 10.0,
    "volume_min": 0.01,
    "volume_max": 100.0,
    "effective_at": "2026-09-01T00:00:00",
    "source": "初始范围",
    "remark": "系统初始化的允许范围",
    "adjust_key": None,
}

# 封闭状态：只能查看，不允许补录、重判或回退到在用。
CLOSED_STATUSES: tuple[str, ...] = ("停用待检", "已报废")
STATUS_ORDER = ["待投用", "在用运行", "停用待检", "已报废"]

RESULT_COMPLIANT = "合规"
RESULT_OVER = "超范围"
RESULT_INCOMPLETE = "待补全"
_RESULT_RANK = {RESULT_OVER: 0, RESULT_COMPLIANT: 1, RESULT_INCOMPLETE: 2}

# 规格必须以数值为主体，允许尾随单位与空白（如 "1.6MPa"、"5 m³"）；
# 夹杂中文的纯文本（如"压力容器样例1"）视为无法识别，归入待补全。
_SPEC_RE = re.compile(r"^\s*([-+]?\d+(?:\.\d+)?)\s*[^\d\-+\s.]*\s*$")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class ComplianceService:
    """合规判定服务。状态全部落在 store 的独立表里，不改动压力容器原表数据。"""

    # ---- 范围版本 -------------------------------------------------

    def _ranges(self) -> list[dict[str, Any]]:
        return sorted(store.rows(RANGE_TABLE), key=lambda row: int(row["revision"]))

    def _ensure_initial_range(self) -> dict[str, Any]:
        rows = store.rows(RANGE_TABLE)
        if not rows:
            rows.append(dict(INITIAL_RANGE))
        return self._ranges()[-1]

    def current_range(self) -> dict[str, Any]:
        self._ensure_initial_range()
        return self._ranges()[-1]

    def range_history(self) -> list[dict[str, Any]]:
        self._ensure_initial_range()
        return [dict(row) for row in self._ranges()]

    def _range_at_registration(self, vessel: dict[str, Any]) -> dict[str, Any]:
        """取容器登记当时生效的范围版本；存量数据没有登记日期时取最早一版。"""
        ranges = self._ranges()
        registered_at = vessel.get("登记日期")
        if isinstance(registered_at, str) and registered_at:
            effective = [r for r in ranges if r["effective_at"] <= registered_at]
            if effective:
                return effective[-1]
        return ranges[0]

    def adjust_range(
        self,
        *,
        pressure_min: float,
        pressure_max: float,
        volume_min: float,
        volume_max: float,
        adjust_key: str | None = None,
        remark: str | None = None,
    ) -> tuple[dict[str, Any], bool, str]:
        """下发一次允许范围调整。

        返回 (范围版本, 是否新建版本, 说明)。同一次调整（幂等键相同）或边界
        与当前版本完全一致时只生效一次：不建新版本、不触发重判。
        """
        invalid = _validate_bounds(pressure_min, pressure_max, volume_min, volume_max)
        if invalid:
            return self.current_range(), False, invalid

        current = self.current_range()
        if adjust_key:
            for row in self._ranges():
                if row.get("adjust_key") == adjust_key:
                    return dict(row), False, "该调整已下发过，按幂等口径只生效一次"

        bounds = (pressure_min, pressure_max, volume_min, volume_max)
        current_bounds = (
            float(current["pressure_min"]),
            float(current["pressure_max"]),
            float(current["volume_min"]),
            float(current["volume_max"]),
        )
        if bounds == current_bounds:
            return current, False, "允许范围与当前版本一致，无需重判"

        new_range = {
            "revision": int(current["revision"]) + 1,
            "pressure_min": pressure_min,
            "pressure_max": pressure_max,
            "volume_min": volume_min,
            "volume_max": volume_max,
            "effective_at": _now(),
            "source": "人工调整",
            "remark": remark or "允许范围调整",
            "adjust_key": adjust_key,
        }
        store.rows(RANGE_TABLE).append(new_range)
        affected = self._rejudge_all(new_range)
        return new_range, True, f"允许范围已更新到第 {new_range['revision']} 版，{affected} 台容器按新范围重判"

    # ---- 判定记录 -------------------------------------------------

    def _records(self) -> list[dict[str, Any]]:
        return store.rows(RECORD_TABLE)

    def latest_record(self, vessel_id: int) -> dict[str, Any] | None:
        records = [r for r in self._records() if int(r["vessel_id"]) == vessel_id]
        return records[-1] if records else None

    def records_of(self, vessel_id: int) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self._records()
            if int(row["vessel_id"]) == vessel_id
        ]

    @staticmethod
    def parse_number(text: Any) -> float | None:
        """从'1.6MPa'、'5 m³'这类规格文本里取数值；空或非数值主体返回 None。"""
        if text is None:
            return None
        match = _SPEC_RE.match(str(text))
        if not match:
            return None
        try:
            value = float(match.group(1))
        except ValueError:
            return None
        if value <= 0:
            return None
        return round(value, 6)

    def _judge(
        self, vessel: dict[str, Any], rng: dict[str, Any]
    ) -> tuple[str, list[str], float | None, float | None]:
        pressure_text = vessel.get("设计压力")
        volume_text = vessel.get("容积规格")
        pressure = self.parse_number(pressure_text)
        volume = self.parse_number(volume_text)

        reasons: list[str] = []
        if pressure is None:
            reasons.append("设计压力缺失或无法识别为数值")
        if volume is None:
            reasons.append("容积规格缺失或无法识别为数值")
        if reasons:
            return RESULT_INCOMPLETE, reasons, pressure, volume

        if not (float(rng["pressure_min"]) <= pressure <= float(rng["pressure_max"])):
            reasons.append(
                f"设计压力 {pressure:g} MPa 超出允许范围 "
                f"[{rng['pressure_min']:g}, {rng['pressure_max']:g}]"
            )
        if not (float(rng["volume_min"]) <= volume <= float(rng["volume_max"])):
            reasons.append(
                f"容积规格 {volume:g} m³ 超出允许范围 "
                f"[{rng['volume_min']:g}, {rng['volume_max']:g}]"
            )
        return (RESULT_OVER if reasons else RESULT_COMPLIANT), reasons, pressure, volume

    def _append_record(
        self,
        vessel: dict[str, Any],
        rng: dict[str, Any],
        source: str,
        *,
        archived: bool = False,
    ) -> dict[str, Any]:
        result, reasons, pressure, volume = self._judge(vessel, rng)
        records = self._records()
        record = {
            "id": max((int(row["id"]) for row in records), default=0) + 1,
            "vessel_id": int(vessel["id"]),
            "result": result,
            "reasons": reasons,
            "pressure_text": vessel.get("设计压力"),
            "pressure_value": pressure,
            "volume_text": vessel.get("容积规格"),
            "volume_value": volume,
            "range_revision": int(rng["revision"]),
            "range_snapshot": {
                "pressure_min": rng["pressure_min"],
                "pressure_max": rng["pressure_max"],
                "volume_min": rng["volume_min"],
                "volume_max": rng["volume_max"],
            },
            "source": source,
            "effective_at": _now(),
            "archived": archived,
        }
        records.append(record)
        return record

    # ---- 业务事件 -------------------------------------------------

    def backfill(self) -> int:
        """给此前没有判定结果的存量容器补一条判定记录。

        取登记当时生效的范围版本；已报废容器的回填记录直接归档。
        已有记录（含已归档历史结论）的容器一律跳过，不推翻历史结论。
        """
        self._ensure_initial_range()
        created = 0
        for vessel in store.rows(VESSEL_MODULE):
            if self.latest_record(int(vessel["id"])) is not None:
                continue
            rng = self._range_at_registration(vessel)
            archived = vessel.get("status") == "已报废"
            self._append_record(vessel, rng, "存量回填", archived=archived)
            created += 1
        return created

    def on_vessel_created(self, vessel: dict[str, Any]) -> dict[str, Any]:
        return self._append_record(vessel, self.current_range(), "登记判定")

    def on_specs_updated(self, vessel: dict[str, Any]) -> dict[str, Any]:
        return self._append_record(vessel, self.current_range(), "资料补全")

    def on_status_changed(
        self, vessel: dict[str, Any], target_status: str
    ) -> dict[str, Any]:
        """状态切换后按当前范围重出判定；报废时把结论归档冻结。"""
        archived = target_status == "已报废"
        return self._append_record(
            vessel, self.current_range(), "状态切换", archived=archived
        )

    def _rejudge_all(self, rng: dict[str, Any]) -> int:
        """按新版本范围重判。封闭容器与已归档记录保持冻结，不参与重判。"""
        affected = 0
        for vessel in store.rows(VESSEL_MODULE):
            vessel_id = int(vessel["id"])
            if vessel.get("status") in CLOSED_STATUSES:
                continue
            latest = self.latest_record(vessel_id)
            if latest is not None and latest.get("archived"):
                continue
            result, _reasons, pressure, volume = self._judge(vessel, rng)
            if pressure is None or volume is None:
                # 待补全与范围无关，范围调整不产生新判定。
                continue
            if latest is not None and int(latest.get("range_revision", 0)) >= int(
                rng["revision"]
            ):
                continue
            self._append_record(vessel, rng, "范围重判")
            affected += 1
        return affected

    # ---- 合规视图 -------------------------------------------------

    def _view_item(self, vessel: dict[str, Any]) -> dict[str, Any]:
        record = self.latest_record(int(vessel["id"]))
        if record is None:  # 理论上 backfill 后不会发生，保险起见补一条。
            record = self._append_record(
                vessel, self._range_at_registration(vessel), "存量回填"
            )
        rng = record["range_snapshot"]
        pressure = self.parse_number(vessel.get("设计压力"))
        volume = self.parse_number(vessel.get("容积规格"))

        def spec(text: Any, value: float | None, low: Any, high: Any) -> dict[str, Any]:
            within: bool | None
            if value is None:
                within = None
            else:
                within = float(low) <= value <= float(high)
            return {"text": text, "value": value, "min": low, "max": high, "within": within}

        item = dict(vessel)
        item["compliance"] = {
            "record_id": record["id"],
            "result": record["result"],
            "reasons": list(record["reasons"]),
            "source": record["source"],
            "range_revision": record["range_revision"],
            "effective_at": record["effective_at"],
            "archived": bool(record.get("archived")),
            "closed": vessel.get("status") in CLOSED_STATUSES,
            "pressure": spec(
                vessel.get("设计压力"), pressure, rng["pressure_min"], rng["pressure_max"]
            ),
            "volume": spec(
                vessel.get("容积规格"), volume, rng["volume_min"], rng["volume_max"]
            ),
        }
        return item

    def build_view(
        self, *, keyword: str | None = None, status: str | None = None
    ) -> dict[str, Any]:
        """组装合规视图：按状态分组、超范围排前、缺失项归入待补全栏。"""
        self.backfill()

        rows = store.rows(VESSEL_MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("容器编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]

        items = [self._view_item(row) for row in rows]
        groups: list[dict[str, Any]] = [
            {"group": name, "kind": "status", "items": []} for name in STATUS_ORDER
        ]
        incomplete = {"group": "待补全", "kind": "incomplete", "items": []}

        for item in items:
            if item["compliance"]["result"] == RESULT_INCOMPLETE:
                incomplete["items"].append(item)
            else:
                groups[STATUS_ORDER.index(item["status"])]["items"].append(item)

        for group in groups + [incomplete]:
            group["items"].sort(
                key=lambda item: (
                    _RESULT_RANK[item["compliance"]["result"]],
                    int(item["id"]),
                )
            )
        groups.append(incomplete)

        summary = {
            "total": len(items),
            "compliant": sum(
                1 for item in items if item["compliance"]["result"] == RESULT_COMPLIANT
            ),
            "out_of_range": sum(
                1 for item in items if item["compliance"]["result"] == RESULT_OVER
            ),
            "incomplete": len(incomplete["items"]),
            "closed": sum(
                1 for item in items if item["compliance"]["closed"]
            ),
        }
        return {
            "current_range": self.current_range(),
            "range_history": self.range_history(),
            "summary": summary,
            "groups": groups,
        }

    def summary(self) -> dict[str, Any]:
        """运营概览用：与未加过滤条件的合规视图同源，保证台数一致。"""
        return self.build_view()["summary"]


def _validate_bounds(
    pressure_min: float,
    pressure_max: float,
    volume_min: float,
    volume_max: float,
) -> str | None:
    values = [
        ("设计压力下限", pressure_min),
        ("设计压力上限", pressure_max),
        ("容积下限", volume_min),
        ("容积上限", volume_max),
    ]
    for label, value in values:
        if not isinstance(value, (int, float)) or value != value:
            return f"{label}必须是数字"
    if pressure_min < 0 or volume_min < 0:
        return "允许范围下限不能为负数"
    if pressure_min > pressure_max:
        return "设计压力下限不能大于上限"
    if volume_min > volume_max:
        return "容积规格下限不能大于上限"
    return None


compliance_service = ComplianceService()
