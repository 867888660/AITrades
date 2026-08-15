from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class ResearchWorkspaceLoadingContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.javascript = (ROOT / "static" / "research_workspace_simple.js").read_text(
            encoding="utf-8"
        )

    def section(self, start: str, end: str) -> str:
        return self.javascript.split(start, 1)[1].split(end, 1)[0]

    def test_base_load_skips_expensive_requirement_status_resolution(self) -> None:
        load_base = self.section("async function loadBase(", "function mergeLibraryAssets")
        self.assertIn("/api/research/project-summaries?limit=500", load_base)
        self.assertIn("/api/research/library?component_type=FACTOR", load_base)
        self.assertIn("/api/research/library?component_type=ALPHA", load_base)
        self.assertNotIn("api('/api/research/library')", load_base)
        self.assertNotIn("component_type=REQUIREMENTS", load_base)

    def test_research_index_uses_one_bulk_summary_without_card_fanout(self) -> None:
        load_base = self.section("async function loadBase(", "function mergeLibraryAssets")
        research_fast_path = load_base.split("if (state.surface === 'research')", 1)[1].split(
            "if (state.surface === 'library')", 1
        )[0]
        self.assertEqual(1, research_fast_path.count("api("))
        self.assertIn("/api/research/project-summaries?limit=500", research_fast_path)
        render_index = self.section(
            "async function renderResearchIndex()", "function renderResearchWorkspace"
        )
        self.assertIn("renderResearchIndexCards()", render_index)
        self.assertNotIn("ensureProjectIndex()", render_index)
        self.assertNotIn("async function researchSummary", self.javascript)
        self.assertNotIn("state.projects.map(researchSummary)", self.javascript)

    def test_opening_research_does_not_trigger_requirement_rebuild(self) -> None:
        detail_load = self.section("async function loadResearch(projectId)", "async function ensureProjectIndex")
        self.assertNotIn("requirements/refresh", detail_load)
        self.assertIn("Requirement maintenance is backend-owned", detail_load)
        self.assertIn("['runs', 'strategy'].includes(state.researchTab)", detail_load)
        self.assertLess(
            detail_load.index("['runs', 'strategy'].includes(state.researchTab)"),
            detail_load.index("factors/sync-library"),
        )

    def test_requirements_are_lazy_and_poll_at_bounded_intervals(self) -> None:
        self.assertIn(
            "api('/api/research/library?component_type=REQUIREMENTS')",
            self.javascript,
        )
        self.assertIn("Loading Requirements\\u2026", self.javascript)
        self.assertIn("live ? 15000 : 60000", self.javascript)
        self.assertNotIn("live ? 2000 : 15000", self.javascript)

    def test_server_rendered_shell_has_visible_loading_feedback(self) -> None:
        template = (ROOT / "templates" / "research_workspace.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("Loading workspace&hellip;", template)
        self.assertIn("research_workspace_simple.js", template)

    def test_library_removal_is_visible_batchable_and_preserves_references(self) -> None:
        self.assertIn("Remove from Library", self.javascript)
        self.assertIn("data-action=\"library-batch-archive\"", self.javascript)
        self.assertIn("async function archiveLibraryAssets", self.javascript)
        self.assertIn("Existing Research references were preserved", self.javascript)
        self.assertNotIn("It must not be used by any Research", self.javascript)

    def test_research_cards_can_be_deleted_without_erasing_history(self) -> None:
        template = (ROOT / "templates" / "research_workspace.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('data-action="delete-research"', template)
        self.assertIn('data-action="delete-research"', self.javascript)
        self.assertIn("async function deleteResearch", self.javascript)
        self.assertIn("/archive`, {method: 'POST'", self.javascript)
        self.assertIn("Immutable Runs, results, lineage, and audit history will be preserved", self.javascript)

    def test_initial_load_failure_replaces_spinner_with_retry(self) -> None:
        self.assertIn("function renderWorkspaceLoadError", self.javascript)
        self.assertIn("data-action=\"retry-workspace-load\"", self.javascript)
        self.assertIn("renderWorkspaceLoadError(error)", self.javascript)


if __name__ == "__main__":
    unittest.main()
