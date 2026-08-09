from __future__ import annotations
import tempfile, unittest
from pathlib import Path
from services.data_platform import DataPlatformStore, RequirementCompiler, ResearchControlPlane, ResolvedDataPlanService

class ResolvedPlanTest(unittest.TestCase):
    def test_grant_scope_is_reused_until_plan_exceeds_it(self):
        with tempfile.TemporaryDirectory() as temp:
            store=DataPlatformStore(Path(temp)/"meta.db"); control=ResearchControlPlane(store)
            project=control.create_project(title="Research",objective="scope"); pid=project["project_id"]
            intent=control.create_plan(project_id=pid,stage="INTENT",payload={"goal":"test"})
            control.create_plan(project_id=pid,stage="RESOLVED",plan_version=intent["plan_version"],payload={"data":"approved scope"})
            grant=control.approve_plan(project_id=pid,plan_version=intent["plan_version"],actor_type="human",
                scope={"allowed_gateways":["openbb"],"allowed_endpoints":["equity.price.historical"],"allowed_providers":["fmp","yfinance"],"allowed_adjustment_modes":["splits_only"]},
                budgets={"max_download_bytes":1000,"max_runtime_seconds":100,"max_backtest_runs":1})
            req=RequirementCompiler(store).compile(project_id=pid,manual_requirements=[{"id":"prices","fields":["close"]}],
                context={"instrument_ids":["equity:XNAS:AAPL"],"frequency":"1d","history_start":"2025-01-01"})
            service=ResolvedDataPlanService(store,Path(temp)/"plans")
            okay=service.create(project_id=pid,logical_name="prices",requirement_set_id=req.requirement_set_id,
                route={"gateway":"openbb","endpoint":"equity.price.historical"},source_policy={"providers":["fmp"]},
                canonical={"adjustment":"splits_only"},estimates={"download_bytes":500,"runtime_seconds":20})
            self.assertTrue(service.validate_grant(okay.artifact_id,grant["grant_id"])["within_scope"])
            outside=service.create(project_id=pid,logical_name="prices",requirement_set_id=req.requirement_set_id,
                route={"gateway":"openbb","endpoint":"equity.price.historical"},source_policy={"providers":["polygon"]},
                canonical={"adjustment":"splits_only"},estimates={"download_bytes":500})
            result=service.validate_grant(outside.artifact_id,grant["grant_id"])
            self.assertFalse(result["within_scope"]); self.assertTrue(result["approval_required"])

if __name__=="__main__": unittest.main()
