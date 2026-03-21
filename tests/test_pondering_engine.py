from __future__ import annotations

from unittest import mock
import unittest

from ocmemog.runtime.memory import pondering_engine


class PonderingEngineMaintenanceTests(unittest.TestCase):
    def test_run_ponder_cycle_triggers_integrity_repair_and_post_check_for_vector_orphan(self) -> None:
        with (
            mock.patch(
                "ocmemog.runtime.memory.pondering_engine.unresolved_state.list_unresolved_state",
                return_value=[],
            ),
            mock.patch(
                "ocmemog.runtime.memory.pondering_engine._candidate_memories",
                return_value=[],
            ),
            mock.patch(
                "ocmemog.runtime.memory.pondering_engine.memory_consolidation.consolidate_memories",
                return_value={"consolidated": [], "reinforcement": []},
            ),
            mock.patch(
                "ocmemog.runtime.memory.pondering_engine.integrity.repair_integrity",
                return_value={"ok": True, "repaired": ["vector_orphan:3"]},
            ) as repair_mock,
            mock.patch(
                "ocmemog.runtime.memory.pondering_engine.vector_index.backfill_missing_vectors",
                return_value=5,
            ) as backfill_mock,
            mock.patch(
                "ocmemog.runtime.memory.pondering_engine.config.OCMEMOG_PONDER_ENABLED",
                "false",
            ),
            mock.patch(
                "ocmemog.runtime.memory.pondering_engine.integrity.run_integrity_check",
                side_effect=[
                    {"issues": ["vector_orphan:3"], "repairable_issues": ["vector_orphan"]},
                    {"issues": ["vector_missing:1"], "repairable_issues": []},
                ],
            ) as integrity_mock,
        ):
            result = pondering_engine.run_ponder_cycle(max_items=3)

        self.assertEqual(integrity_mock.call_count, 2)
        repair_mock.assert_called_once()
        backfill_mock.assert_called_once()
        maintenance = result["maintenance"]
        self.assertIn("repair", maintenance)
        self.assertTrue(any(issue.startswith("vector_missing") for issue in maintenance.get("issues", [])))
        self.assertEqual(maintenance["vector_backfill"], 5)

    def test_run_ponder_cycle_runs_backfill_when_vector_missing_issue_present(self) -> None:
        with (
            mock.patch(
                "ocmemog.runtime.memory.pondering_engine.unresolved_state.list_unresolved_state",
                return_value=[],
            ),
            mock.patch(
                "ocmemog.runtime.memory.pondering_engine._candidate_memories",
                return_value=[],
            ),
            mock.patch(
                "ocmemog.runtime.memory.pondering_engine.memory_consolidation.consolidate_memories",
                return_value={"consolidated": [], "reinforcement": []},
            ),
            mock.patch(
                "ocmemog.runtime.memory.pondering_engine.integrity.repair_integrity",
            ) as repair_mock,
            mock.patch(
                "ocmemog.runtime.memory.pondering_engine.vector_index.backfill_missing_vectors",
                return_value=11,
            ) as backfill_mock,
            mock.patch(
                "ocmemog.runtime.memory.pondering_engine.config.OCMEMOG_PONDER_ENABLED",
                "false",
            ),
            mock.patch(
                "ocmemog.runtime.memory.pondering_engine.integrity.run_integrity_check",
                return_value={"issues": ["vector_missing:11"], "repairable_issues": []},
            ),
        ):
            result = pondering_engine.run_ponder_cycle(max_items=3)

        repair_mock.assert_not_called()
        backfill_mock.assert_called_once()
        self.assertEqual(result["maintenance"]["vector_backfill"], 11)
        self.assertIn("vector_missing:11", result["maintenance"]["issues"])

    def test_run_ponder_cycle_handles_non_dict_integrity_payload(self) -> None:
        with (
            mock.patch(
                "ocmemog.runtime.memory.pondering_engine.unresolved_state.list_unresolved_state",
                return_value=[],
            ),
            mock.patch(
                "ocmemog.runtime.memory.pondering_engine._candidate_memories",
                return_value=[],
            ),
            mock.patch(
                "ocmemog.runtime.memory.pondering_engine.memory_consolidation.consolidate_memories",
                return_value={"consolidated": [], "reinforcement": []},
            ),
            mock.patch(
                "ocmemog.runtime.memory.pondering_engine.integrity.repair_integrity",
            ) as repair_mock,
            mock.patch(
                "ocmemog.runtime.memory.pondering_engine.vector_index.backfill_missing_vectors",
            ) as backfill_mock,
            mock.patch(
                "ocmemog.runtime.memory.pondering_engine.config.OCMEMOG_PONDER_ENABLED",
                "false",
            ),
            mock.patch(
                "ocmemog.runtime.memory.pondering_engine.integrity.run_integrity_check",
                return_value=["vector_missing:11"],
            ),
        ):
            result = pondering_engine.run_ponder_cycle(max_items=3)

        repair_mock.assert_not_called()
        backfill_mock.assert_not_called()
        self.assertEqual(result["maintenance"], {"issues": [], "repairable_issues": [], "ok": False})


if __name__ == "__main__":
    unittest.main()
