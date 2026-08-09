import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from services.strategy_chart_service import (
    _chart_event_item,
    _load_virtual_tick_metric_samples,
    _market_series_items,
    _metric_series_items,
)
from services.strategy_display import is_opaque_market_identifier, preferred_leg_display_name


ROOT = Path(__file__).resolve().parents[2]


class StrategyWorkspaceTradeUiTests(unittest.TestCase):
    def test_polymarket_question_wins_over_condition_id_snapshot(self):
        condition_id = "0x8b32c0c5f081501ef936b71876df0028613a1a159c5e1bae1279e087086cc7d5"
        name = preferred_leg_display_name(
            {
                "asset_class": "polymarket_binary",
                "venue": "polymarket",
                "condition_id": condition_id,
                "display_name": condition_id,
                "instrument_json": {
                    "question": "Will Apple be the second-largest company in the world by market cap on July 31?"
                },
            },
            {"question": condition_id},
            fallback="Leg 1",
        )

        self.assertTrue(is_opaque_market_identifier(condition_id))
        self.assertEqual(
            name,
            "Will Apple be the second-largest company in the world by market cap on July 31?",
        )

    def test_chart_event_keeps_fill_identity_for_tooltip(self):
        item = _chart_event_item(
            {
                "id": "order-574",
                "ts": "2026-07-18T00:32:47.803690+00:00",
                "event_type": "trade",
                "event_subtype": "filled",
                "summary": "BUY Yes qty=30.57 @0.3431 taker",
                "severity": "info",
                "source": "virtual",
                "payload": {
                    "action": "BUY",
                    "leg": 4,
                    "side": "Yes",
                    "status": "filled",
                    "price": 0.3431454311295187,
                    "qty": 30.565119912790692,
                    "fee": 0.344463583,
                    "order_ref": "574",
                    "raw_action": {"large": "payload must not leak into chart"},
                },
            }
        )

        self.assertEqual(item["status"], "filled")
        self.assertEqual(item["action"], "BUY")
        self.assertEqual(item["side"], "YES")
        self.assertEqual(item["leg"], 4)
        self.assertAlmostEqual(item["price"], 0.3431454311295187)
        self.assertAlmostEqual(item["quantity"], 30.565119912790692)
        self.assertNotIn("raw_action", item["payload"])

    def test_market_series_exposes_compact_and_complete_names(self):
        series = _market_series_items(
            [
                {
                    "leg_index": 4,
                    "detail": {
                        "question": "Will Apple be the largest company in the world by market cap on December 31?",
                        "group_item_title": "Apple",
                        "condition_id": "condition-apple",
                    },
                }
            ],
            "all",
        )

        self.assertTrue(series)
        self.assertEqual(series[0]["market_short_label"], "Apple")
        self.assertIn("Will Apple be the largest company", series[0]["full_label"])

    def test_chart_print_event_does_not_ship_embedded_debug_json_in_label(self):
        item = _chart_event_item(
            {
                "id": "print-1",
                "ts": "2026-07-25T13:00:00+00:00",
                "event_type": "print",
                "summary": (
                    "now_time=2026-07-25T13:00:00+00:00 decision=HOLD actions=0 "
                    "candidates=4 selected=3 machine_state=auto "
                    "===DB_JSON_BEGIN=== {\"large\":\"payload\"} ===DB_JSON_END==="
                ),
            }
        )

        self.assertEqual(
            item["label"],
            "decision=HOLD · actions=0 · candidates=4 · selected=3 · machine_state=auto",
        )
        self.assertNotIn("DB_JSON", item["label"])

    def test_leg_position_strategy_metric_is_separate_from_actual_position_panel(self):
        series = _metric_series_items(
            ["L4_yes_position"],
            {
                "metric_catalog": {
                    "items": [
                        {
                            "key": "L4_yes_position",
                            "label": "L4 Apple Yes Position",
                            "panel": "leg_positions",
                            "unit": "percent",
                        }
                    ]
                }
            },
        )

        self.assertEqual(series[0]["panel"], "leg_position_metrics")
        self.assertTrue(series[0]["label"].startswith("Leg-cap metric"))

    def test_frontend_contract_keeps_visible_legend_and_trade_status(self):
        template = (ROOT / "templates" / "strategy_workspace.html").read_text(encoding="utf-8")
        script = (ROOT / "static" / "strategy_workspace_v2.js").read_text(encoding="utf-8")
        styles = (ROOT / "static" / "workspace_v3.css").read_text(encoding="utf-8")
        cards = (ROOT / "static" / "workspace_v3_patch.js").read_text(encoding="utf-8")

        self.assertIn('id="chartSeriesLegend"', template)
        self.assertIn('id="chartTradeStatus"', template)
        self.assertIn("function renderInlineSeriesLegend", script)
        self.assertIn("data-inline-legend-toggle", script)
        self.assertIn("加载额外数据", script)
        self.assertIn('overlayLoaderGroup("crypto", "Crypto")', script)
        self.assertIn('overlayLoaderGroup("finance", "Stock")', script)
        self.assertIn("data-inline-overlay-toggle", script)
        self.assertIn("function readableTrackedMarketName", script)
        self.assertIn('tooltip: { trigger: "axis", triggerOn: "mousemove|click"', script)
        self.assertIn("function setupChartPointerTracking", script)
        self.assertIn('viewport.addEventListener("mousemove", rememberPointerPosition, true)', script)
        self.assertNotIn("pixelSegmentDistance", script)
        self.assertNotIn("chartFallbackHoveredSeriesId", script)
        self.assertIn("function setupNativeLineTooltipFocus", script)
        self.assertIn('chart.on("mouseover", (params)', script)
        self.assertIn('const nativeSeriesId = String(chartNativeHoveredSeriesId || "")', script)
        self.assertIn('chart.dispatchAction({ type: "hideTip" })', script)
        self.assertIn("const items = hasNativeFocusedLine ? nativeHoveredItems : rawItems", script)
        self.assertIn("function renderLatestTradeStatus", script)
        self.assertIn("positionQtyBefore", script)
        self.assertIn("visible: (seriesStyleState[s.key]", script)
        self.assertIn("position: sticky", styles)
        self.assertIn("线段管理", template)
        self.assertIn('title="${esc(cardTooltip)}"', cards)
        self.assertIn('legPnlRaw == null || legPnlRaw === "" ? null', cards)

    def test_chart_keeps_visible_subchart_controls_events_and_direct_manipulation(self):
        template = (ROOT / "templates" / "strategy_workspace.html").read_text(encoding="utf-8")
        script = (ROOT / "static" / "strategy_workspace_v2.js").read_text(encoding="utf-8")
        styles = (ROOT / "static" / "workspace_binance_chart.css").read_text(encoding="utf-8")

        self.assertEqual(template.count('id="chartMetricPicker"'), 1)
        self.assertLess(template.index('id="chartMetricPicker"'), template.index('id="workspaceChartMeta"'))
        self.assertIn('id="chartMetricsToggle"', template)
        self.assertIn('id="chartStatusToggle"', template)
        self.assertIn('id="workspaceEventsPanel" open', template)
        self.assertIn("let eventsRequestedByUser = true", script)
        self.assertIn("function isEventTimelineSelected()", script)
        self.assertIn("moveOnMouseMove: true", script)
        self.assertIn("zoomOnMouseWheel: true", script)
        self.assertIn("function setupDirectAxisScaling", script)
        self.assertIn('getZr().on("dblclick"', script)
        self.assertIn("await Promise.all([workspaceLoad, chartLoad])", script)
        self.assertIn("min-height: 18px", styles)

    def test_timeline_controller_keeps_navigation_local_and_history_incremental(self):
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        template = (ROOT / "templates" / "strategy_workspace.html").read_text(encoding="utf-8")
        script = (ROOT / "static" / "strategy_workspace_v2.js").read_text(encoding="utf-8")
        cards = (ROOT / "static" / "workspace_v3_patch.js").read_text(encoding="utf-8")

        for element_id in (
            "chartLiveModeBtn",
            "chartInspectStatus",
            "chartReturnLatestBtn",
            "chartFocusTime",
            "chartFitHeightBtn",
            "chartFullscreenBtn",
            "workspaceEventDetails",
        ):
            self.assertIn(f'id="{element_id}"', template)
        self.assertIn("const CHART_MAX_HEIGHT = 4200", script)
        self.assertIn("function activateTimelinePreset", script)
        self.assertIn("applyLocalTimeWindow(window.from, window.to", script)
        self.assertNotIn("scheduleEarlierHistoryLoad(dataZoomState) {\n  const start", script)
        self.assertIn("distanceFromLeft > 0.20", script)
        self.assertIn("/chart-history?", script)
        self.assertIn("setupShiftRangeSelection", script)
        self.assertIn('setTimelineMode("INSPECT", "pan-or-zoom")', script)
        self.assertIn('event.key.toLowerCase()', script)
        self.assertIn("window.focusWorkspaceEvent", script)
        self.assertIn('data-event-id="${esc(eventId)}"', cards)
        self.assertIn("/chart-history", app_source)

    def test_event_inspect_stays_local_and_metric_hydration_uses_cached_extent(self):
        template = (ROOT / "templates" / "strategy_workspace.html").read_text(encoding="utf-8")
        script = (ROOT / "static" / "strategy_workspace_v2.js").read_text(encoding="utf-8")
        styles = (ROOT / "static" / "workspace_binance_chart.css").read_text(encoding="utf-8")

        self.assertIn('<aside id="workspaceEventDetails"', template)
        self.assertNotIn("scrollIntoView", script)
        self.assertIn("function revealWorkspaceEventInList", script)
        self.assertIn("function navigateWorkspaceEvent", script)
        self.assertIn('params.set("from", new Date(cachedExtent.minTs).toISOString())', script)
        self.assertIn('params.set("to", new Date(cachedExtent.maxTs).toISOString())', script)
        self.assertIn(".ws3-event-details.is-open", styles)
        self.assertIn("position: fixed", styles)

    def test_virtual_tick_metric_sample_does_not_clear_omitted_metrics(self):
        connection = MagicMock()
        connection.execute.return_value.fetchall.return_value = [
            {
                "run_at_utc": "2026-07-24T14:07:00+00:00",
                "function_json": '{"metrics":{"yes_off":6.0}}',
            }
        ]

        with patch("services.strategy_chart_service.strategy_data_source.connect", return_value=connection):
            samples = _load_virtual_tick_metric_samples(
                {"row_id": 87},
                ["yes_off", "risk_scale"],
                "2026-07-24T14:00:00+00:00",
                "2026-07-24T15:00:00+00:00",
                300,
            )

        sample = next(iter(samples.values()))
        self.assertEqual(sample["metric:yes_off"], 6.0)
        self.assertNotIn("metric:risk_scale", sample)


if __name__ == "__main__":
    unittest.main()
