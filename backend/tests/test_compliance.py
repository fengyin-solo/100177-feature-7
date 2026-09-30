"""压力容器合规判定规则测试（标准库 unittest，不引入额外依赖）。

覆盖：
- 合规视图分组、超范围排前、缺失归待补全；
- 状态切换重判、封闭态只能查看且不能回退在用；
- 范围调整重判、幂等键去重、历史判定按当时范围保留；
- 存量回填按登记当时范围、不推翻已归档历史结论；
- 运营概览超范围台数与视图条数一致；
- 存量容器原始数据不被改写。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.compliance import (  # noqa: E402
    RESULT_COMPLIANT,
    RESULT_INCOMPLETE,
    RESULT_OVER,
    ComplianceService,
    compliance_service,
)
from app.services.vessel import VesselService  # noqa: E402
from app.store import store  # noqa: E402


class ComplianceCase(unittest.TestCase):
    def setUp(self) -> None:
        store.reset()
        # 范围版本表与判定记录表也随 reset 清空，服务每次重新计算。
        store.rows("vessel_range_version").clear()
        store.rows("vessel_compliance_record").clear()
        self.vessel = VesselService()
        self.compliance = compliance_service

    def _create(
        self, code: str, pressure: str = "", volume: str = "", status_action: str | None = None
    ) -> dict:
        entry, missing = self.vessel.create_entry(
            {"容器编号": code, "容器名称": code, "设计压力": pressure, "容积规格": volume}
        )
        self.assertFalse(missing, f"登记缺少字段：{missing}")
        if status_action:
            updated, msg = self.vessel.run_action(int(entry["id"]), status_action)
            self.assertIsNotNone(updated, msg)
        return entry

    def test_backfill_existing_vessels_incomplete_without_touching_data(self) -> None:
        # 存量 3 条示例数据的设计压力/容积都是非数值文本 → 回填为待补全
        view = self.compliance.build_view()
        self.assertEqual(view["summary"]["total"], 3)
        self.assertEqual(view["summary"]["incomplete"], 3)
        self.assertEqual(view["summary"]["out_of_range"], 0)
        # 每条存量容器恰好回填一条记录
        for vessel_id in (1, 2, 3):
            records = self.compliance.records_of(vessel_id)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["source"], "存量回填")
            self.assertEqual(records[0]["range_revision"], 1)
        # 回填不改变存量原始数据
        seeds = store.rows("vessel")
        self.assertEqual(seeds[0]["设计压力"], "压力容器样例1")
        self.assertEqual(seeds[0].get("容积规格"), "压力容器样例1")

    def test_backfill_runs_once_and_uses_registration_range(self) -> None:
        self.compliance.build_view()
        self.compliance.build_view()  # 再访问不重复回填
        self.assertEqual(len(self.compliance.records_of(1)), 1)

    def test_grouped_view_out_of_range_first(self) -> None:
        self._create("V-OK", "1.6", "5", "办理投用")        # 范围内（在用）
        self._create("V-HI", "50", "200", "办理投用")  # 双超，在用组
        view = self.compliance.build_view()
        groups = {g["group"]: g["items"] for g in view["groups"]}

        active = groups["在用运行"]
        self.assertTrue(active, "在用组应有数据")
        # 超范围的排在组内最前
        self.assertEqual(active[0]["容器编号"], "V-HI")
        self.assertEqual(active[0]["compliance"]["result"], RESULT_OVER)
        self.assertEqual(active[1]["容器编号"], "V-OK")
        self.assertEqual(active[1]["compliance"]["result"], RESULT_COMPLIANT)

        # 合规视图列：值、范围、是否在内
        spec = active[0]["compliance"]["pressure"]
        self.assertEqual(spec["value"], 50.0)
        self.assertFalse(spec["within"])
        self.assertEqual(spec["min"], 0.1)
        self.assertEqual(spec["max"], 10.0)

    def test_missing_specs_go_to_incomplete_group(self) -> None:
        self._create("V-MISS", "1.6", "")  # 容积缺失
        view = self.compliance.build_view()
        groups = {g["group"]: g for g in view["groups"]}
        self.assertNotIn("V-MISS", [i["容器编号"] for i in groups["待投用"]["items"]])
        codes = [i["容器编号"] for i in groups["待补全"]["items"]]
        self.assertIn("V-MISS", codes)

    def test_status_switch_rejudges_and_closed_cannot_revert(self) -> None:
        entry = self._create("V-S1", "1.6", "5")
        # 办理投用 → 新判定记录
        updated, _ = self.vessel.run_action(int(entry["id"]), "办理投用")
        self.assertEqual(updated["status"], "在用运行")
        self.assertEqual(self.compliance.latest_record(int(entry["id"]))["source"], "状态切换")

        # 停用待检后不能再办理投用（回退在用）
        updated, _ = self.vessel.run_action(int(entry["id"]), "安排检验")
        self.assertEqual(updated["status"], "停用待检")
        again, msg = self.vessel.run_action(int(entry["id"]), "办理投用")
        self.assertIsNone(again)
        self.assertIn("只能查看", msg)

        # 封闭态不能补录资料
        patched, spec_msg = self.vessel.update_specs(
            int(entry["id"]), {"设计压力": "9.9", "容积规格": "5"}
        )
        self.assertIsNone(patched)
        self.assertIn("只允许查看", spec_msg)

        # 封闭态除回退外的其他状态迁出同样只读（如停用待检 → 直接报废）
        scrap, scrap_msg = self.vessel.run_action(int(entry["id"]), "报废容器")
        self.assertIsNone(scrap)
        self.assertIn("只能查看", scrap_msg)

    def test_scrapped_record_is_archived_and_kept_on_backfill_rejudge(self) -> None:
        # 报废一台容器 → 记录归档；之后回填/重判都不推翻
        entry = self._create("V-OLD", "50", "50")  # 超范围（但合规口径先归档历史结论）
        updated, _ = self.vessel.run_action(int(entry["id"]), "报废容器")
        self.assertEqual(updated["status"], "已报废")
        archived = self.compliance.latest_record(int(entry["id"]))
        self.assertTrue(archived["archived"])
        self.assertEqual(archived["result"], RESULT_OVER)

        # 模拟"存量容器此前没有判定结果之外"的场景：归档结论不被任何后续动作覆盖
        rng, changed, _ = self.compliance.adjust_range(
            pressure_min=0.01,
            pressure_max=100.0,
            volume_min=0.01,
            volume_max=500.0,
            adjust_key="loosen",
        )
        self.assertTrue(changed)
        latest = self.compliance.latest_record(int(entry["id"]))
        self.assertTrue(latest["archived"], "归档记录必须保留为当前结论")
        self.assertEqual(latest["result"], RESULT_OVER, "归档的历史结论不被重判推翻")

        # 已报废也不能回退在用
        again, msg = self.vessel.run_action(int(entry["id"]), "办理投用")
        self.assertIsNone(again)
        self.assertIn("只能查看", msg)

    def test_range_adjust_rejudges_open_vessels_but_keeps_history_snapshot(self) -> None:
        self._create("V-R1", "1.6", "5")
        self._create("V-R2", "9", "90")
        # 收紧上限到 8：V-R1 仍合规，V-R2 双超
        rng, changed, msg = self.compliance.adjust_range(
            pressure_min=0.1,
            pressure_max=8,
            volume_min=0.01,
            volume_max=80,
            adjust_key="tight-001",
        )
        self.assertTrue(changed, msg)
        self.assertEqual(rng["revision"], 2)
        view = self.compliance.build_view()
        results = {
            item["容器编号"]: item["compliance"]["result"]
            for group in view["groups"]
            for item in group["items"]
        }
        self.assertEqual(results["V-R1"], RESULT_COMPLIANT)
        self.assertEqual(results["V-R2"], RESULT_OVER)

        # 新记录挂在新版本，旧记录仍保留在第 1 版范围快照上
        records = self.compliance.records_of(
            int(store.rows("vessel")[3]["id"])
        )
        revisions = [r["range_revision"] for r in records]
        self.assertEqual(revisions, [1, 2])
        self.assertEqual(records[0]["range_snapshot"]["pressure_max"], 10.0)
        self.assertEqual(records[1]["range_snapshot"]["pressure_max"], 8)

    def test_duplicate_adjust_key_applies_once(self) -> None:
        self._create("V-D", "1.6", "5")
        kwargs = dict(
            pressure_min=0.1,
            pressure_max=6,
            volume_min=0.01,
            volume_max=50,
            adjust_key="dup-key",
        )
        rng1, changed1, _ = self.compliance.adjust_range(**kwargs)
        rng2, changed2, _ = self.compliance.adjust_range(**kwargs)
        self.assertTrue(changed1)
        self.assertFalse(changed2)
        self.assertEqual(rng1["revision"], rng2["revision"])
        # 只重判一次：V-D 只有 登记判定 + 一次范围重判
        vessel_id = int(store.rows("vessel")[3]["id"])
        self.assertEqual(len(self.compliance.records_of(vessel_id)), 2)

        # 同样边界不带幂等键也不重复生效
        rng3, changed3, _ = self.compliance.adjust_range(
            pressure_min=0.1, pressure_max=6, volume_min=0.01, volume_max=50
        )
        self.assertFalse(changed3)

    def test_incomplete_not_rejudged_on_range_change(self) -> None:
        entry = self._create("V-INC", "1.6", "")
        self.compliance.adjust_range(
            pressure_min=0.1,
            pressure_max=6,
            volume_min=0.01,
            volume_max=50,
            adjust_key="k-1",
        )
        # 待补全与范围无关，不产生范围重判记录
        self.assertEqual(len(self.compliance.records_of(int(entry["id"]))), 1)

        # 补全容积后按当前范围立即重判
        patched, msg = self.vessel.update_specs(int(entry["id"]), {"容积规格": "40"})
        self.assertIsNotNone(patched, msg)
        latest = self.compliance.latest_record(int(entry["id"]))
        self.assertEqual(latest["source"], "资料补全")
        self.assertEqual(latest["result"], RESULT_COMPLIANT)

    def test_overview_matches_view_count(self) -> None:
        self._create("V-O1", "50", "5")
        self._create("V-O2", "1.6", "5")
        view = self.compliance.build_view()
        self.assertEqual(
            self.compliance.summary()["out_of_range"], view["summary"]["out_of_range"]
        )

    def test_filter_by_status_and_keyword(self) -> None:
        self._create("V-F1", "50", "5", "办理投用")
        view = self.compliance.build_view(status="在用运行", keyword="V-F1")
        codes = [
            item["容器编号"]
            for group in view["groups"]
            for item in group["items"]
        ]
        self.assertEqual(codes, ["V-F1"])


if __name__ == "__main__":
    unittest.main()
