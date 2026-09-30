"""压力容器合规判定：允许范围版本化、判定记录只追加、存量回填与口径统一。

设计约束（对应业务规则）：

* 允许范围按版本下发，调整后按新版本重新判定，历史记录保留当时的范围快照；
* 同一次调整（客户端令牌）或同一组范围值重复下发，只生效一次；
* 判定记录只追加不修改，当前结论永远取该容器最新一条；
* 存量容器（此前没有判定结果）按登记当时生效的范围回填一条，已归档的历史结论不被推翻；
* 设计压力或容积规格缺失/无法识别的，结论为「待补全」；
* 运营概览的超范围台数与合规视图共用 ``latest_view`` 同一口径。
"""
from __future__ import annotations

import re
from typing import Any

from app.store import store

MODULE = "vessel"
RANGES_TABLE = "vessel_ranges"
JUDGMENTS_TABLE = "vessel_judgments"
TOKENS_TABLE = "vessel_range_tokens"

# 登记台账没有合规判定功能之前，统一按这份基线范围管理；版本号固定为 v0。
BASELINE_VERSION = "v0"
BASELINE_EFFECTIVE_AT = "2026-01-01T00:00:00"
DEFAULT_RANGE = {
    "pressure_min": 0.1,
    "pressure_max": 10.0,
    "volume_min": 0.05,
    "volume_max": 100.0,
}

IN_SERVICE_STATUS = "在用运行"
LOCKED_STATUSES = {"停用待检", "已报废"}
STATUS_BUCKETS = ["待投用", "在用运行", "停用待检", "已报废"]
PENDING_BUCKET = "待补全"

# 规格值只接受「数值 + 可选单位」（如 1.6 MPa、5 m3、120 m³）；
# 「压力容器样例1」这类夹着数字的说明文本不是规格，按缺失处理。
_NUMBER_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*[A-Za-z0-9./^³μµ]*\s*$")


def parse_number(value: Any) -> float | None:
    """把「1.6 MPa」「50 m³」这类带单位文本解析成数值；空值或非规格文本返回 None。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _NUMBER_RE.match(str(value))
    return float(match.group(1)) if match else None


def _now() -> str:
    from datetime import datetime

    return datetime.now().replace(microsecond=0).isoformat()


class VesselComplianceService:
    """合规判定全部读写都经过这里，保证视图、动作、概览口径一致。"""

    def __init__(self) -> None:
        self._ready = False

    # ---- 初始化与存量回填 ------------------------------------------------

    def ensure_ready(self) -> None:
        if self._ready:
            return
        if not store.rows(RANGES_TABLE):
            store.rows(RANGES_TABLE).append({
                "version": BASELINE_VERSION,
                "source": "基线范围",
                "effective_at": BASELINE_EFFECTIVE_AT,
                **DEFAULT_RANGE,
            })
        self._backfill_legacy()
        self._ready = True

    def _backfill_legacy(self) -> None:
        """给没有判定记录的存量容器，按登记当时生效的范围补一条判定。

        已有记录的容器一律跳过——回填只补不盖，归档过的历史结论保持原样。
        """
        judged = {int(row["vessel_id"]) for row in store.rows(JUDGMENTS_TABLE)}
        for vessel in store.rows(MODULE):
            vessel_id = int(vessel["id"])
            if vessel_id in judged:
                continue
            range_row = self._range_effective_at(self._registered_at(vessel))
            self._append_judgment(
                vessel,
                range_row,
                source="存量回填",
                judged_at=self._registered_at(vessel) + "T00:00:00",
            )

    @staticmethod
    def _registered_at(vessel: dict[str, Any]) -> str:
        value = vessel.get("registered_at")
        if value:
            return str(value)[:10]
        # 存量数据没有登记时间字段，视为基线范围生效当日登记。
        return BASELINE_EFFECTIVE_AT[:10]

    # ---- 范围版本 --------------------------------------------------------

    def _range_effective_at(self, day: str) -> dict[str, Any]:
        """取某一时点生效的范围版本（版本按生效时间倒序，找第一个不晚于该日的）。"""
        versions = sorted(
            store.rows(RANGES_TABLE),
            key=lambda row: str(row["effective_at"]),
            reverse=True,
        )
        stamp = day if "T" in day else f"{day}T23:59:59"
        for row in versions:
            if str(row["effective_at"]) <= stamp:
                return row
        return versions[-1]

    def current_range(self) -> dict[str, Any]:
        self.ensure_ready()
        return store.rows(RANGES_TABLE)[-1]

    def list_ranges(self) -> list[dict[str, Any]]:
        self.ensure_ready()
        return list(store.rows(RANGES_TABLE))

    def adjust_range(
        self,
        values: dict[str, Any],
        *,
        token: str | None = None,
    ) -> tuple[dict[str, Any] | None, str, bool]:
        """下发新的允许范围。

        返回 (新版本, 消息, 是否生效)；重复下发返回当前版本且 applied=False，不重判。
        """
        self.ensure_ready()
        token = (token or "").strip() or None
        if token:
            for old in store.rows(TOKENS_TABLE):
                if old["token"] == token:
                    return old["range"], "同一次范围调整已生效，重复下发不再处理", False

        try:
            bounds = {key: float(values[key]) for key in DEFAULT_RANGE}
        except (KeyError, TypeError, ValueError):
            return None, "范围参数缺失或不是数字，本次调整未生效", False

        if not (bounds["pressure_min"] <= bounds["pressure_max"]
                and bounds["volume_min"] <= bounds["volume_max"]):
            return None, "范围下限不能大于上限，本次调整未生效", False
        if any(value <= 0 for value in bounds.values()):
            return None, "允许范围必须为正数，本次调整未生效", False

        current = self.current_range()
        fingerprint = tuple(bounds[key] for key in DEFAULT_RANGE)
        current_fingerprint = tuple(current[key] for key in DEFAULT_RANGE)
        if fingerprint == current_fingerprint:
            # 没带令牌但范围与当前版本一致，同样视为重复下发，只生效一次。
            if token:
                store.rows(TOKENS_TABLE).append({"token": token, "range": current})
            return current, "允许范围与当前版本一致，无需重新判定", False

        version = f"v{len(store.rows(RANGES_TABLE))}"
        range_row = {
            "version": version,
            "source": "调整下发",
            "effective_at": _now(),
            "token": token,
            **bounds,
        }
        store.rows(RANGES_TABLE).append(range_row)
        if token:
            store.rows(TOKENS_TABLE).append({"token": token, "range": range_row})
        self._rejudge_all(range_row, source="范围调整")
        return range_row, f"允许范围已更新为 {version}，全部容器已按新范围重判", True

    # ---- 判定记录（只追加） ----------------------------------------------

    def _append_judgment(
        self,
        vessel: dict[str, Any],
        range_row: dict[str, Any],
        *,
        source: str,
        judged_at: str | None = None,
    ) -> dict[str, Any]:
        rows = store.rows(JUDGMENTS_TABLE)
        pressure = parse_number(vessel.get("设计压力"))
        volume = parse_number(vessel.get("容积规格"))
        missing = []
        if pressure is None:
            missing.append("设计压力")
        if volume is None:
            missing.append("容积规格")

        if missing:
            result, reason = PENDING_BUCKET, f"缺少{'、'.join(missing)}，待补全"
        else:
            over = []
            if pressure < range_row["pressure_min"]:
                over.append(f"设计压力 {pressure:g} 低于下限 {range_row['pressure_min']:g}")
            elif pressure > range_row["pressure_max"]:
                over.append(f"设计压力 {pressure:g} 超过上限 {range_row['pressure_max']:g}")
            if volume < range_row["volume_min"]:
                over.append(f"容积规格 {volume:g} 低于下限 {range_row['volume_min']:g}")
            elif volume > range_row["volume_max"]:
                over.append(f"容积规格 {volume:g} 超过上限 {range_row['volume_max']:g}")
            if over:
                result, reason = "超范围", "；".join(over)
            else:
                result, reason = "合规", "设计压力与容积规格均在允许范围内"

        record = {
            "id": max((int(row["id"]) for row in rows), default=0) + 1,
            "vessel_id": int(vessel["id"]),
            "version": range_row["version"],
            "result": result,
            "reason": reason,
            "missing": missing,
            "source": source,
            "judged_at": judged_at or _now(),
            "registered_at": self._registered_at(vessel),
            "pressure_value": pressure,
            "volume_value": volume,
            # 范围快照：历史判定永远按当时范围呈现，不随后续调整漂移。
            "range_snapshot": {key: range_row[key] for key in DEFAULT_RANGE},
        }
        rows.append(record)
        return record

    def _rejudge_all(self, range_row: dict[str, Any], *, source: str) -> int:
        count = 0
        for vessel in store.rows(MODULE):
            self._append_judgment(vessel, range_row, source=source)
            count += 1
        return count

    def rejudge_vessel(self, vessel: dict[str, Any], *, source: str) -> dict[str, Any]:
        """登记、补全规格或状态切换后，按当前范围给单台容器补一条判定。"""
        self.ensure_ready()
        return self._append_judgment(vessel, self.current_range(), source=source)

    def list_judgments(self, vessel_id: int) -> list[dict[str, Any]]:
        self.ensure_ready()
        return [
            dict(row) for row in store.rows(JUDGMENTS_TABLE)
            if int(row["vessel_id"]) == vessel_id
        ]

    # ---- 视图口径 --------------------------------------------------------

    def latest_map(self) -> dict[int, dict[str, Any]]:
        """每台容器最新一条判定（追加序最后一条即最新），视图与概览共用。"""
        self.ensure_ready()
        latest: dict[int, dict[str, Any]] = {}
        for row in store.rows(JUDGMENTS_TABLE):
            latest[int(row["vessel_id"])] = row
        return latest

    def latest_view(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        latest = self.latest_map()
        vessels = store.rows(MODULE)
        if keyword:
            vessels = [row for row in vessels if keyword in str(row.get("容器编号", ""))]
        if status:
            vessels = [row for row in vessels if row.get("status") == status]

        groups: dict[str, list[dict[str, Any]]] = {
            bucket: [] for bucket in STATUS_BUCKETS + [PENDING_BUCKET]
        }
        for vessel in vessels:
            judgment = latest.get(int(vessel["id"]))
            item = self._decorate(vessel, judgment)
            if item["compliance"]["result"] == PENDING_BUCKET:
                groups[PENDING_BUCKET].append(item)
            else:
                groups.setdefault(vessel.get("status") or "未知状态", [])
                groups[vessel.get("status") or "未知状态"].append(item)

        # 超范围的标记并排前面；组内其余按容器编号稳定排列。
        for items in groups.values():
            items.sort(key=lambda item: (
                0 if item["compliance"]["result"] == "超范围" else 1,
                str(item.get("容器编号", "")),
            ))

        ordered = [
            {"status": bucket, "items": groups[bucket]}
            for bucket in STATUS_BUCKETS + [PENDING_BUCKET]
            if groups.get(bucket)
        ]
        # 超范围台数按本次视图条数计（过滤条件下仍与视图一致）；
        # 全局口径由 over_range_count 单独提供给运营概览。
        over_range_in_view = sum(
            1 for group in ordered for item in group["items"]
            if item["compliance"]["result"] == "超范围"
        )
        latest = self.latest_map()
        pending_global = sum(
            1 for vessel in store.rows(MODULE)
            if (row := latest.get(int(vessel["id"]))) is not None and row["result"] == PENDING_BUCKET
        )
        return {
            "groups": ordered,
            "total": len(vessels),
            "overRange": over_range_in_view,
            "overRangeGlobal": self.over_range_count(),
            "pendingGlobal": pending_global,
            "currentRange": self.current_range(),
            "ranges": self.list_ranges(),
        }

    @staticmethod
    def _decorate(vessel: dict[str, Any], judgment: dict[str, Any] | None) -> dict[str, Any]:
        item = dict(vessel)
        if judgment is None:
            compliance = {"result": PENDING_BUCKET, "reason": "尚未判定，待补全", "version": None}
        else:
            compliance = {
                "result": judgment["result"],
                "reason": judgment["reason"],
                "version": judgment["version"],
                "judgedAt": judgment["judged_at"],
                "source": judgment["source"],
                "rangeSnapshot": judgment["range_snapshot"],
                "missing": judgment.get("missing", []),
            }
        compliance["locked"] = vessel.get("status") in LOCKED_STATUSES
        item["compliance"] = compliance
        return item

    def over_range_count(self) -> int:
        """运营概览超范围台数：与合规视图里的超范围条数同源。"""
        latest = self.latest_map()
        return sum(
            1 for vessel in store.rows(MODULE)
            if (row := latest.get(int(vessel["id"]))) is not None and row["result"] == "超范围"
        )


compliance_service = VesselComplianceService()
