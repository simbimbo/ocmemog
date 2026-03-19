from __future__ import annotations

import os
import tempfile
import unittest

from brain.runtime.memory import store
from ocmemog.sidecar import app


class IngestClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["OCMEMOG_STATE_DIR"] = self.tempdir.name
        store._SCHEMA_READY = False

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        os.environ.pop("OCMEMOG_STATE_DIR", None)
        store._SCHEMA_READY = False

    def test_explicit_preference_requested_as_reflection_routes_to_preferences(self) -> None:
        response = app._ingest_request(
            app.IngestRequest(
                content="I prefer quiet boutique hotels when I travel.",
                kind="memory",
                memory_type="reflections",
                source="unit-test",
            )
        )

        self.assertTrue(response["ok"])
        self.assertEqual(response["memory_type"], "preferences")
        self.assertEqual(response["reference"], "preferences:1")

    def test_identity_statement_requested_as_reflection_routes_to_identity(self) -> None:
        response = app._ingest_request(
            app.IngestRequest(
                content="My name is Sam and I live in Boston.",
                kind="memory",
                memory_type="reflections",
                source="unit-test",
            )
        )

        self.assertTrue(response["ok"])
        self.assertEqual(response["memory_type"], "identity")
        self.assertEqual(response["reference"], "identity:1")

    def test_reflective_statement_stays_in_reflections(self) -> None:
        response = app._ingest_request(
            app.IngestRequest(
                content="Reflection: I should slow down and verify the assumptions before acting.",
                kind="memory",
                memory_type="reflections",
                source="unit-test",
            )
        )

        self.assertTrue(response["ok"])
        self.assertEqual(response["memory_type"], "reflections")
        self.assertEqual(response["reference"], "reflections:1")

    def test_pronouns_statement_requested_as_reflection_routes_to_identity(self) -> None:
        response = app._ingest_request(
            app.IngestRequest(
                content="My pronouns are she/her.",
                kind="memory",
                memory_type="reflections",
                source="unit-test",
            )
        )

        self.assertTrue(response["ok"])
        self.assertEqual(response["memory_type"], "identity")
        self.assertEqual(response["reference"], "identity:1")

    def test_contact_statement_requested_as_reflection_routes_to_identity(self) -> None:
        response = app._ingest_request(
            app.IngestRequest(
                content="My email is sam@example.com.",
                kind="memory",
                memory_type="reflections",
                source="unit-test",
            )
        )

        self.assertTrue(response["ok"])
        self.assertEqual(response["memory_type"], "identity")
        self.assertEqual(response["reference"], "identity:1")

    def test_allergy_statement_requested_as_reflection_routes_to_identity(self) -> None:
        response = app._ingest_request(
            app.IngestRequest(
                content="I'm allergic to peanuts.",
                kind="memory",
                memory_type="reflections",
                source="unit-test",
            )
        )

        self.assertTrue(response["ok"])
        self.assertEqual(response["memory_type"], "identity")
        self.assertEqual(response["reference"], "identity:1")

    def test_speculative_preference_stays_in_reflections(self) -> None:
        response = app._ingest_request(
            app.IngestRequest(
                content="I think I prefer the earlier flight, but I'm not sure yet.",
                kind="memory",
                memory_type="reflections",
                source="unit-test",
            )
        )

        self.assertTrue(response["ok"])
        self.assertEqual(response["memory_type"], "reflections")
        self.assertEqual(response["reference"], "reflections:1")

    def test_identity_progress_statement_stays_in_reflections(self) -> None:
        response = app._ingest_request(
            app.IngestRequest(
                content="I'm thinking through whether I should move closer to work.",
                kind="memory",
                memory_type="reflections",
                source="unit-test",
            )
        )

        self.assertTrue(response["ok"])
        self.assertEqual(response["memory_type"], "reflections")
        self.assertEqual(response["reference"], "reflections:1")

    def test_long_statement_stays_in_reflections(self) -> None:
        response = app._ingest_request(
            app.IngestRequest(
                content="I prefer quiet hotels. " * 20,
                kind="memory",
                memory_type="reflections",
                source="unit-test",
            )
        )

        self.assertTrue(response["ok"])
        self.assertEqual(response["memory_type"], "reflections")
        self.assertEqual(response["reference"], "reflections:1")


if __name__ == "__main__":
    unittest.main()
