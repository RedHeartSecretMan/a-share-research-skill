from __future__ import annotations

import importlib.metadata
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.installation_health import check_installation  # noqa: E402


class InstallationHealthTests(unittest.TestCase):
    def locked_versions(self) -> dict[str, str]:
        lock = (
            Path(__file__).resolve().parents[1]
            / "skill"
            / "a-share-research"
            / "requirements.lock"
        )
        versions: dict[str, str] = {}
        for raw_line in lock.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            requirement, _, marker = line.partition(";")
            distribution, version = requirement.strip().split("==", 1)
            if marker and f'"{sys.platform}"' not in marker:
                continue
            versions[distribution] = version
        return versions

    def test_locked_dependency_version_and_module_load_are_both_required(self) -> None:
        loaded: list[str] = []

        def load(module: str) -> object:
            loaded.append(module)
            operation_classes = {
                "a_share_research.communication_sources": (
                    "MootdxF10Operation",
                    "mootdx_f10@1",
                ),
                "a_share_research.intraday_replay_mootdx": (
                    "MootdxIntradayReplayOperation",
                    "mootdx_intraday_replay@1",
                ),
                "a_share_research.intraday_sources": (
                    "TongdaxinIntradayOperation",
                    "tongdaxin_intraday_snapshot@1",
                ),
            }
            operation = operation_classes.get(module)
            if operation is None:
                return object()
            class_name, operation_id = operation
            return SimpleNamespace(
                **{class_name: SimpleNamespace(operation_id=operation_id)}
            )

        health = check_installation(
            version=self.locked_versions().__getitem__,
            load_module=load,
        )

        self.assertEqual(health["status"], "complete")
        self.assertIn("mootdx.quotes", loaded)
        self.assertIn("a_share_research.communication_sources", loaded)
        self.assertEqual(
            {item["status"] for item in health["dependencies"]},  # type: ignore[union-attr]
            {"complete"},
        )
        self.assertEqual(
            {item["status"] for item in health["operations"]},  # type: ignore[union-attr]
            {"complete"},
        )

    def test_version_mismatch_is_incomplete_without_loading_candidate_code(
        self,
    ) -> None:
        def must_not_load(module: str) -> object:
            if module == "mootdx.quotes":
                raise AssertionError("version-mismatched dependency must not load")
            return object()

        locked = self.locked_versions()
        health = check_installation(
            version=lambda distribution: (
                "0.11.6" if distribution == "mootdx" else locked[distribution]
            ),
            load_module=must_not_load,
        )

        self.assertEqual(health["status"], "installation_incomplete")
        self.assertEqual(
            next(
                item
                for item in health["dependencies"]  # type: ignore[union-attr]
                if item["distribution"] == "mootdx"
            ),
            {
                "distribution": "mootdx",
                "required_version": "0.11.7",
                "installed_version": "0.11.6",
                "status": "version_mismatch",
                "modules": ["mootdx.quotes"],
                "operation_ids": [
                    "mootdx_f10@1",
                    "mootdx_intraday_replay@1",
                    "tongdaxin_intraday_snapshot@1",
                ],
            },
        )

    def test_dependency_metadata_or_module_failure_is_sanitized_and_closed(
        self,
    ) -> None:
        health = check_installation(
            version=lambda _: (_ for _ in ()).throw(OSError("/secret/package/path")),
            load_module=lambda _: object(),
        )
        serialized = str(health)
        self.assertEqual(health["status"], "installation_incomplete")
        self.assertEqual(
            {item["status"] for item in health["dependencies"]},  # type: ignore[union-attr]
            {"load_failed"},
        )
        self.assertNotIn("/secret/package/path", serialized)

        def failed_mootdx_load(module: str) -> object:
            if module == "mootdx.quotes":
                raise RuntimeError("credential=secret")
            return object()

        module_health = check_installation(
            version=self.locked_versions().__getitem__,
            load_module=failed_mootdx_load,
        )
        serialized = str(module_health)
        mootdx = next(
            item
            for item in module_health["dependencies"]  # type: ignore[union-attr]
            if item["distribution"] == "mootdx"
        )
        self.assertEqual(module_health["status"], "installation_incomplete")
        self.assertEqual(mootdx["status"], "load_failed")
        self.assertNotIn("credential=secret", serialized)

    def test_missing_distribution_is_reported_without_exception_text(self) -> None:
        def missing(_: str) -> str:
            raise importlib.metadata.PackageNotFoundError("mootdx / private path")

        health = check_installation(version=missing)

        self.assertEqual(health["status"], "installation_incomplete")
        self.assertEqual(
            {item["status"] for item in health["dependencies"]},  # type: ignore[union-attr]
            {"missing"},
        )
        self.assertTrue(
            all(
                item["installed_version"] is None
                for item in health["dependencies"]  # type: ignore[union-attr]
            )
        )
        self.assertNotIn("private", str(health))


if __name__ == "__main__":
    unittest.main()
