from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.result_artifact import (  # noqa: E402
    DeterministicFilesystemAdapter,
    ResultArtifact,
    ResultArtifactIdentity,
)
from a_share_research.result_artifact_inputs import (  # noqa: E402
    prepare_candidate,
    prepare_completed_partition,
)


def artifact_identity() -> ResultArtifactIdentity:
    return ResultArtifactIdentity(
        artifact_schema_version="1.0",
        request_bytes=b'{"request":"semantic"}',
        producer_bytes=b"producer-commit:fixed",
        plan_bytes=b'{"plan":"fundamental_research_priority@1.0"}',
        operation_manifest_bytes=b'{"operations":[]}',
    )


def artifact_coverage() -> dict[str, dict[str, int]]:
    return {
        exchange: {
            key: 0
            for key in (
                "acquired",
                "expected",
                "failed",
                "ranked",
                "resolved",
                "unranked",
                "unsupported",
            )
        }
        for exchange in ("BSE", "SSE", "SZSE")
    }


def partition(index: int = 0):
    return prepare_completed_partition(
        kind="universe",
        exchange="SSE",
        shard_index=index,
        schema_version="1.0",
        records=({"canonical_security_id": "SSE:600519", "score": "1.20"},),
    )


class _IsolatedArtifactTest(unittest.TestCase):
    def setUp(self) -> None:
        from unittest.mock import patch
        import a_share_research.result_artifact as module

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.anchor_root = (
            Path(temporary.name).resolve() / ".state" / "result-artifact-anchors" / "v1"
        )
        isolated = patch.object(module, "_DEFAULT_ANCHOR_ROOT", self.anchor_root)
        isolated.start()
        self.addCleanup(isolated.stop)


class ResultArtifactTests(_IsolatedArtifactTest):
    def test_complete_lifecycle_returns_original_receipt_on_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "artifact"
            identity = artifact_identity()
            handle = ResultArtifact.create(root, identity)
            handle.checkpoint()
            handle.write_completed_partition(partition())
            handle.write_candidate(
                prepare_candidate(
                    canonical_security_id="SSE:600519",
                    schema_version="1.0",
                    document={"status": "limited"},
                )
            )
            receipt = handle.finalize(coverage=artifact_coverage())
            self.assertEqual(receipt["file_count"], 2)
            self.assertEqual(receipt["record_count"], 2)
            self.assertEqual(ResultArtifact.resume(root, identity), receipt)
            self.assertEqual(
                ResultArtifact.recover_finalized_receipt(root, identity), receipt
            )
            ResultArtifact.validate_receipt(root, identity, receipt)
            with self.assertRaises(ValueError):
                handle.write_completed_partition(partition(1))
            self.assertEqual(
                (root / partition().relative_path).read_bytes(),
                b'{"canonical_security_id":"SSE:600519","score":"1.20"}\n',
            )

    def test_two_handles_merge_completed_files_and_keep_write_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "artifact"
            identity = artifact_identity()
            first = ResultArtifact.create(root, identity)
            second = ResultArtifact.resume(root, identity)
            self.assertIsInstance(second, ResultArtifact)
            first.write_completed_partition(partition())
            second.write_completed_partition(partition(1))
            with self.assertRaises(ValueError):
                first.write_completed_partition(partition(1))
            self.assertEqual(
                first.finalize(coverage=artifact_coverage())["file_count"], 2
            )

    def test_deterministic_adapter_matches_production_public_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "artifact"
            identity = artifact_identity()
            production = ResultArtifact.create(root, identity)
            fake = DeterministicFilesystemAdapter(Path(temporary).resolve() / "model")
            deterministic = ResultArtifact.create(root, identity, filesystem=fake)
            production.write_completed_partition(partition())
            deterministic.write_completed_partition(partition())
            self.assertEqual(
                fake.read_bytes(root, "checkpoint.json"),
                (root / "checkpoint.json").read_bytes(),
            )
            receipt = production.finalize(coverage=artifact_coverage())
            self.assertEqual(
                deterministic.finalize(coverage=artifact_coverage()), receipt
            )
            self.assertEqual(
                fake.read_bytes(root, "manifest.json"),
                (root / "manifest.json").read_bytes(),
            )
            self.assertEqual(
                json.loads(fake.read_bytes(root, "manifest.json"))["status"],
                "finalized",
            )


class ResultArtifactRecoveryTests(_IsolatedArtifactTest):
    def test_each_publication_interruption_is_recoverable_and_write_once(self) -> None:
        phases = (
            "after_journal_publication",
            "after_final_publication",
            "after_checkpoint_replacement",
            "after_stage_cleanup_item",
            "after_stage_cleanup",
        )
        for phase in phases:
            with self.subTest(phase=phase):
                filesystem = DeterministicFilesystemAdapter(
                    Path("/deterministic/model")
                )
                root, identity = Path("/caller/artifact"), artifact_identity()
                handle = ResultArtifact.create(root, identity, filesystem=filesystem)
                filesystem.interrupt_next(phase)
                with self.assertRaises(KeyboardInterrupt):
                    handle.write_completed_partition(partition())
                restored = ResultArtifact.resume(root, identity, filesystem=filesystem)
                self.assertIsInstance(restored, ResultArtifact)
                with self.assertRaises(ValueError):
                    restored.write_completed_partition(partition())
                self.assertEqual(
                    restored.finalize(coverage=artifact_coverage())["file_count"], 1
                )

    def test_manifest_commit_without_receipt_recovers_identically(self) -> None:
        for phase in (
            "after_journal_publication",
            "after_manifest_publication",
            "after_stage_cleanup_item",
            "after_stage_cleanup",
        ):
            with self.subTest(phase=phase):
                filesystem = DeterministicFilesystemAdapter(
                    Path("/deterministic/model")
                )
                root, identity = Path("/caller/artifact"), artifact_identity()
                handle = ResultArtifact.create(root, identity, filesystem=filesystem)
                handle.write_completed_partition(partition())
                filesystem.interrupt_next(phase)
                with self.assertRaises(KeyboardInterrupt):
                    handle.finalize(coverage=artifact_coverage())
                first = ResultArtifact.resume(root, identity, filesystem=filesystem)
                self.assertIsInstance(first, dict)
                snapshot = filesystem.inventory(root)
                second = ResultArtifact.resume(root, identity, filesystem=filesystem)
                self.assertEqual(second, first)
                self.assertEqual(filesystem.inventory(root), snapshot)

    def test_unknown_last_entry_blocks_recovery_without_any_mutation(self) -> None:
        for action in ("create", "write", "finalize"):
            with self.subTest(action=action):
                filesystem = DeterministicFilesystemAdapter(
                    Path("/deterministic/model")
                )
                root, identity = Path("/caller/artifact"), artifact_identity()
                if action == "create":
                    filesystem.interrupt_next("after_create_publication")
                    with self.assertRaises(KeyboardInterrupt):
                        ResultArtifact.create(root, identity, filesystem=filesystem)
                else:
                    handle = ResultArtifact.create(
                        root, identity, filesystem=filesystem
                    )
                    filesystem.interrupt_next("after_journal_publication")
                    with self.assertRaises(KeyboardInterrupt):
                        if action == "write":
                            handle.write_completed_partition(partition())
                        else:
                            handle.finalize(coverage=artifact_coverage())
                filesystem.tamper(root, "zz-unknown", b"caller bytes")
                snapshot = filesystem.inventory(root)
                with self.assertRaises(ValueError):
                    ResultArtifact.resume(root, identity, filesystem=filesystem)
                self.assertEqual(filesystem.inventory(root), snapshot)
                self.assertEqual(
                    filesystem.read_bytes(root, "zz-unknown"), b"caller bytes"
                )

    def test_preapply_inventory_drift_refuses_before_journal_publication(self) -> None:
        filesystem = DeterministicFilesystemAdapter(Path("/deterministic/model"))
        root, identity = Path("/caller/artifact"), artifact_identity()
        handle = ResultArtifact.create(root, identity, filesystem=filesystem)
        filesystem.pause_next_before_apply(
            lambda: filesystem.tamper(root, "unknown", b"outside")
        )
        with self.assertRaises(ValueError):
            handle.write_completed_partition(partition())
        self.assertEqual(
            set(
                n.relative_path
                for n in filesystem.inventory(root).nodes
                if n.kind == "file"
            ),
            {
                "checkpoint.json",
                "unknown",
                next(
                    n.relative_path
                    for n in filesystem.inventory(root).nodes
                    if n.relative_path.startswith(".protocol/create-")
                ),
            },
        )

    def test_deterministic_adapter_operates_when_production_descriptors_are_disabled(
        self,
    ) -> None:
        from unittest.mock import patch
        import a_share_research.result_artifact as module

        filesystem = DeterministicFilesystemAdapter(Path("/deterministic/model"))
        root, identity = Path("/caller/artifact"), artifact_identity()
        with patch.object(module, "_USE_ANCHORED_DIRECTORY_FD", False):
            handle = ResultArtifact.create(root, identity, filesystem=filesystem)
            handle.write_completed_partition(partition())
            receipt = handle.finalize(coverage=artifact_coverage())
            self.assertEqual(
                ResultArtifact.resume(root, identity, filesystem=filesystem), receipt
            )

    def test_new_inode_with_identical_protocol_bytes_is_unowned(self) -> None:
        filesystem = DeterministicFilesystemAdapter(Path("/deterministic/model"))
        root, identity = Path("/caller/artifact"), artifact_identity()
        handle = ResultArtifact.create(root, identity, filesystem=filesystem)
        handle.write_completed_partition(partition())
        handle.finalize(coverage=artifact_coverage())
        journal = next(
            n
            for n in filesystem.inventory(root).nodes
            if n.relative_path.startswith(".protocol/txn-")
        )
        filesystem.tamper(
            root, journal.relative_path, journal.encoded, replace_identity=True
        )
        snapshot = filesystem.inventory(root)
        with self.assertRaises(ValueError):
            ResultArtifact.resume(root, identity, filesystem=filesystem)
        self.assertEqual(filesystem.inventory(root), snapshot)

    def test_file_count_envelope_uses_maximal_schema_counts_and_identity_width(
        self,
    ) -> None:
        proof = ResultArtifact.checkpoint_capacity(
            artifact_identity(), partition_count=9, candidate_count=20
        )
        self.assertEqual(proof["file_count"], 29)
        self.assertEqual(proof["identity_component_bits"], 64)
        self.assertEqual(proof["identity_component_bytes"], 16)
        self.assertLessEqual(proof["maximum_checkpoint_bytes"], 65_536)
        with self.assertRaises(ValueError):
            ResultArtifact.checkpoint_capacity(
                artifact_identity(), partition_count=1_000, candidate_count=20
            )


_PROCESS = r"""
import json, os, sys
from pathlib import Path
from tests.test_result_artifact import artifact_identity, artifact_coverage, partition, frozen_context
from a_share_research.result_artifact import ProductionFilesystemAdapter, ResultArtifact
class CrashAdapter(ProductionFilesystemAdapter):
    seen = 0
    def _phase(self, phase):
        wanted, _, occurrence = sys.argv[3].partition("@")
        if phase == wanted:
            self.seen += 1
            if self.seen == int(occurrence or "1"):
                os._exit(77)
root, action = Path(sys.argv[1]), sys.argv[2]
identity, adapter = artifact_identity(), CrashAdapter(anchor_root=Path(sys.argv[4]))
if action in {"create", "context-create"}:
    result = ResultArtifact.create(root, identity, filesystem=adapter,
                                   initial_context=frozen_context() if action == "context-create" else None)
else:
    result = ResultArtifact.resume(root, identity, filesystem=adapter)
    if action == "write":
        result.write_completed_partition(partition())
    elif action == "finalize":
        result = result.finalize(coverage=artifact_coverage())
if isinstance(result, dict):
    print(json.dumps(result, sort_keys=True))
else:
    print("partial")
"""


def filesystem_tree(root: Path):
    return tuple(
        sorted(
            (
                path.relative_to(root).as_posix(),
                path.lstat().st_dev,
                path.lstat().st_ino,
                path.lstat().st_mtime_ns,
                path.lstat().st_mode,
                path.read_bytes() if path.is_file() and not path.is_symlink() else b"",
            )
            for path in (root, *root.rglob("*"))
        )
    )


class ResultArtifactProductionRecoveryTests(_IsolatedArtifactTest):
    def run_process(self, root: Path, action: str, phase: str = ""):
        import os
        import subprocess
        import sys

        return subprocess.run(
            [
                sys.executable,
                "-c",
                _PROCESS,
                str(root),
                action,
                phase,
                str(self.anchor_root),
            ],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

    def assert_process_ok(self, result) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_real_independent_exit_and_fresh_process_recovery_matrix(self) -> None:
        matrix = (
            ("create", "after_create_publication"),
            ("write", "after_journal_publication"),
            ("write", "after_final_publication"),
            ("write", "after_checkpoint_replacement"),
            ("write", "after_stage_cleanup_item"),
            ("write", "after_stage_cleanup"),
            ("finalize", "after_journal_publication"),
            ("finalize", "after_manifest_publication"),
            ("finalize", "after_stage_cleanup_item"),
            ("finalize", "after_stage_cleanup"),
        )
        for action, phase in matrix:
            with (
                self.subTest(action=action, phase=phase),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary).resolve() / "artifact"
                identity = artifact_identity()
                if action != "create":
                    handle = ResultArtifact.create(root, identity)
                    if action == "finalize":
                        handle.write_completed_partition(partition())
                crashed = self.run_process(root, action, phase)
                self.assertEqual(crashed.returncode, 77, crashed.stderr)
                self.assertEqual(crashed.stdout, "")
                restored = self.run_process(root, "resume")
                self.assert_process_ok(restored)
                if action == "finalize":
                    receipt = json.loads(restored.stdout)
                    stable = filesystem_tree(root)
                    second = self.run_process(root, "resume")
                    self.assert_process_ok(second)
                    self.assertEqual(json.loads(second.stdout), receipt)
                    self.assertEqual(filesystem_tree(root), stable)
                else:
                    self.assertEqual(restored.stdout.strip(), "partial")
                    handle = ResultArtifact.resume(root, identity)
                    if action == "write":
                        with self.assertRaises(ValueError):
                            handle.write_completed_partition(partition())
                    self.assertEqual(
                        handle.finalize(coverage=artifact_coverage())["file_count"],
                        0 if action == "create" else 1,
                    )

    def test_recovery_is_itself_interrupted_then_third_process_finishes(self) -> None:
        for finalizing in (False, True):
            with (
                self.subTest(finalizing=finalizing),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary).resolve() / "artifact"
                identity = artifact_identity()
                handle = ResultArtifact.create(root, identity)
                if finalizing:
                    handle.write_completed_partition(partition())
                first = self.run_process(
                    root,
                    "finalize" if finalizing else "write",
                    "after_manifest_publication"
                    if finalizing
                    else "after_final_publication",
                )
                self.assertEqual(first.returncode, 77, first.stderr)
                second = self.run_process(
                    root,
                    "resume",
                    "after_stage_cleanup_item"
                    if finalizing
                    else "after_checkpoint_replacement",
                )
                self.assertEqual(second.returncode, 77, second.stderr)
                third = self.run_process(root, "resume")
                self.assert_process_ok(third)
                restored = ResultArtifact.resume(root, identity)
                receipt = (
                    restored
                    if isinstance(restored, dict)
                    else restored.finalize(coverage=artifact_coverage())
                )
                self.assertEqual(receipt["file_count"], 1)
                control = ResultArtifact.create(
                    Path(temporary).resolve() / "control", identity
                )
                control.write_completed_partition(partition())
                self.assertEqual(
                    control.finalize(coverage=artifact_coverage()), receipt
                )

    def test_real_file_and_directory_fsync_failures_never_return_success(self) -> None:
        import os
        import stat
        from unittest.mock import patch
        from a_share_research.result_artifact import ProductionFilesystemAdapter

        original_fsync = os.fsync
        for kind in ("file", "directory"):
            with (
                self.subTest(kind=kind),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary).resolve() / "artifact"
                identity = artifact_identity()

                class FailingAdapter(ProductionFilesystemAdapter):
                    armed = False

                    def _phase(self, phase):
                        if phase == (
                            "after_checkpoint_replacement"
                            if kind == "file"
                            else "after_final_publication"
                        ):
                            self.armed = True

                adapter = FailingAdapter()
                handle = ResultArtifact.create(root, identity, filesystem=adapter)
                failed = []

                def fsync(descriptor):
                    regular = stat.S_ISREG(os.fstat(descriptor).st_mode)
                    if adapter.armed and regular == (kind == "file"):
                        failed.append(descriptor)
                        raise OSError("real fsync injected failure")
                    return original_fsync(descriptor)

                with patch(
                    "a_share_research.result_artifact.os.fsync", side_effect=fsync
                ):
                    with self.assertRaises(ValueError):
                        handle.write_completed_partition(partition())
                self.assertTrue(failed)
                restored = self.run_process(root, "resume")
                self.assert_process_ok(restored)
                recovered = ResultArtifact.resume(root, identity)
                self.assertEqual(
                    recovered.finalize(coverage=artifact_coverage())["file_count"], 1
                )

    def test_sealed_unknown_entry_and_same_byte_inode_replacement_are_zero_mutation(
        self,
    ) -> None:
        for changed in ("unknown", "journal", "content", "manifest"):
            with (
                self.subTest(changed=changed),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary).resolve() / "artifact"
                identity = artifact_identity()
                handle = ResultArtifact.create(root, identity)
                handle.write_completed_partition(partition())
                handle.finalize(coverage=artifact_coverage())
                if changed == "unknown":
                    (root / "unknown").write_bytes(b"caller-owned")
                else:
                    target = (
                        next((root / ".protocol").glob("txn-*.json"))
                        if changed == "journal"
                        else root
                        / (
                            partition().relative_path
                            if changed == "content"
                            else "manifest.json"
                        )
                    )
                    replacement = Path(temporary).resolve() / "external-new-inode"
                    replacement.write_bytes(target.read_bytes())
                    replacement.replace(target)
                before = filesystem_tree(root)
                with self.assertRaises(ValueError):
                    ResultArtifact.resume(root, identity)
                self.assertEqual(filesystem_tree(root), before)

    def test_owned_pending_state_with_last_unknown_entry_has_zero_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "artifact"
            identity = artifact_identity()
            ResultArtifact.create(root, identity)
            result = self.run_process(root, "write", "after_journal_publication")
            self.assertEqual(result.returncode, 77, result.stderr)
            (root / "zz-unknown").write_bytes(b"caller-owned")
            before = filesystem_tree(Path(temporary).resolve())
            with self.assertRaises(ValueError):
                ResultArtifact.resume(root, identity)
            self.assertEqual(filesystem_tree(Path(temporary).resolve()), before)


class ResultArtifactConfinementTests(_IsolatedArtifactTest):
    def test_raw_production_persistence_is_confined_and_alias_gate_is_sensitive(
        self,
    ) -> None:
        import ast
        import a_share_research.result_artifact as module

        raw_names = {
            "open",
            "close",
            "dup",
            "fstat",
            "fsync",
            "read",
            "write",
            "mkdir",
            "link",
            "rename",
            "rmdir",
            "stat",
            "unlink",
            "listdir",
            "flock",
            "CDLL",
        }

        class Probe(ast.NodeVisitor):
            def __init__(self):
                self.classes = []
                self.objects = {"os", "fcntl", "ctypes"}
                self.callables = set()
                self.violations = []

            def inside(self):
                return "ProductionFilesystemAdapter" in self.classes

            def visit_ClassDef(self, node):
                self.classes.append(node.name)
                self.generic_visit(node)
                self.classes.pop()

            def raw_object(self, node):
                return isinstance(node, ast.Name) and node.id in self.objects

            def raw_callable(self, node):
                if isinstance(node, ast.Name):
                    return node.id in self.callables
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in {"staticmethod", "classmethod"}
                    and node.args
                ):
                    return self.raw_callable(node.args[0])
                return (
                    isinstance(node, ast.Attribute)
                    and node.attr in raw_names
                    and self.raw_object(node.value)
                )

            def assignment(self, targets, value, line):
                if self.inside():
                    return
                for target in targets:
                    if self.raw_object(value) or self.raw_callable(value):
                        self.violations.append(line)
                        if isinstance(target, ast.Name):
                            (
                                self.objects
                                if self.raw_object(value)
                                else self.callables
                            ).add(target.id)

            def visit_Assign(self, node):
                self.assignment(node.targets, node.value, node.lineno)
                self.generic_visit(node)

            def visit_AnnAssign(self, node):
                if node.value is not None:
                    self.assignment((node.target,), node.value, node.lineno)
                self.generic_visit(node)

            def visit_Import(self, node):
                if not self.inside():
                    for alias in node.names:
                        if alias.name in {"os", "fcntl", "ctypes"}:
                            self.objects.add(alias.asname or alias.name)
                self.generic_visit(node)

            def visit_ImportFrom(self, node):
                if not self.inside() and node.module in {"os", "fcntl", "ctypes"}:
                    for alias in node.names:
                        if alias.name in raw_names:
                            self.callables.add(alias.asname or alias.name)
                            self.violations.append(node.lineno)
                self.generic_visit(node)

            def visit_Call(self, node):
                if not self.inside() and self.raw_callable(node.func):
                    self.violations.append(node.lineno)
                if (
                    not self.inside()
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "getattr"
                    and len(node.args) >= 2
                    and self.raw_object(node.args[0])
                    and isinstance(node.args[1], ast.Constant)
                    and node.args[1].value in raw_names
                ):
                    self.violations.append(node.lineno)
                self.generic_visit(node)

        actual = Probe()
        actual.visit(ast.parse(Path(module.__file__).read_text()))
        self.assertEqual(actual.violations, [])
        for source in (
            "os.open('x', 0)",
            "raw = os\nraw.open('x', 0)",
            "from os import open as persist\npersist('x', 0)",
            "persist = os.open\npersist('x', 0)",
            "globals()['raw'] = os",
            "holder.raw = os",
            "holder.persist = os.open",
            "persist = staticmethod(os.open)",
            "getattr(os, 'unlink')('x')",
            "ctypes.CDLL(None)",
        ):
            with self.subTest(source=source):
                probe = Probe()
                probe.visit(ast.parse(source))
                self.assertTrue(probe.violations)

    def test_large_nonnegative_coverage_retains_the_existing_serialized_bound(
        self,
    ) -> None:
        filesystem = DeterministicFilesystemAdapter(Path("/deterministic/model"))
        root, identity = Path("/caller/artifact"), artifact_identity()
        handle = ResultArtifact.create(root, identity, filesystem=filesystem)
        coverage = artifact_coverage()
        coverage["SSE"]["expected"] = 10**20
        receipt = handle.finalize(coverage=coverage)
        self.assertEqual(receipt["file_count"], 0)
        self.assertEqual(
            json.loads(filesystem.read_bytes(root, "manifest.json"))["coverage"]["SSE"][
                "expected"
            ],
            10**20,
        )


def frozen_context():
    return prepare_completed_partition(
        kind="universe",
        exchange="SSE",
        shard_index=0,
        schema_version="screening-context@1.0",
        records=(
            {
                "requested_as_of": "2026-10-13",
                "research_now": "2026-10-13T10:00:00+08:00",
                "information_cutoff": "2026-10-13T10:00:00+08:00",
                "screen_date": "2026-10-12",
                "calendar_evidence_refs": ["calendar-proof-1"],
            },
        ),
    )


class ResultArtifactContextTests(_IsolatedArtifactTest):
    def test_initial_context_is_frozen_without_pretending_a_shard_is_complete(
        self,
    ) -> None:
        filesystem = DeterministicFilesystemAdapter(Path("/deterministic/model"))
        root, identity = Path("/caller/artifact"), artifact_identity()
        prepared = frozen_context()
        handle = ResultArtifact.create(
            root, identity, initial_context=prepared, filesystem=filesystem
        )
        checkpoint = handle.checkpoint()
        self.assertEqual(checkpoint["completed_files"], [])
        self.assertEqual(
            checkpoint["frozen_context"]["research_now"], "2026-10-13T10:00:00+08:00"
        )
        self.assertEqual(checkpoint["identity"]["context_sha256"], prepared.sha256)
        self.assertFalse(
            any(
                n.relative_path == prepared.relative_path
                for n in filesystem.inventory(root).nodes
            )
        )
        # The same actual coordinate remains available to a real universe shard.
        handle.write_completed_partition(partition())
        returned = handle.checkpoint()
        checkpoint["frozen_context"]["research_now"] = "2026-10-14T10:00:00+08:00"
        self.assertEqual(
            returned["frozen_context"]["research_now"], "2026-10-13T10:00:00+08:00"
        )
        self.assertEqual(handle.finalize(coverage=artifact_coverage())["file_count"], 1)

    def test_context_tamper_with_recomputed_local_digest_is_zero_mutation(self) -> None:
        from hashlib import sha256

        filesystem = DeterministicFilesystemAdapter(Path("/deterministic/model"))
        root, identity = Path("/caller/artifact"), artifact_identity()
        handle = ResultArtifact.create(
            root, identity, initial_context=frozen_context(), filesystem=filesystem
        )
        checkpoint = handle.checkpoint()
        checkpoint["frozen_context"]["information_cutoff"] = "2026-10-14T23:59:59+08:00"
        record = (
            json.dumps(
                checkpoint["frozen_context"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            + b"\n"
        )
        checkpoint["identity"]["context_sha256"] = sha256(record).hexdigest()
        changed = (
            json.dumps(
                checkpoint, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
            + b"\n"
        )
        filesystem.tamper(root, "checkpoint.json", changed)
        before = filesystem.inventory(root)
        with self.assertRaises(ValueError):
            ResultArtifact.resume(root, identity, filesystem=filesystem)
        self.assertEqual(filesystem.inventory(root), before)


class ResultArtifactContextBoundaryTests(_IsolatedArtifactTest):
    run_process = ResultArtifactProductionRecoveryTests.run_process
    assert_process_ok = ResultArtifactProductionRecoveryTests.assert_process_ok

    def test_create_crash_before_first_shard_preserves_original_frozen_context(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "artifact"
            result = self.run_process(
                root, "context-create", "after_create_publication"
            )
            self.assertEqual(result.returncode, 77, result.stderr)
            restored = self.run_process(root, "resume")
            self.assert_process_ok(restored)
            handle = ResultArtifact.resume(root, artifact_identity())
            checkpoint = handle.checkpoint()
            self.assertEqual(checkpoint["completed_files"], [])
            self.assertEqual(
                checkpoint["frozen_context"]["research_now"],
                "2026-10-13T10:00:00+08:00",
            )
            self.assertEqual(checkpoint["frozen_context"]["screen_date"], "2026-10-12")
            handle.write_completed_partition(partition())
            receipt = handle.finalize(coverage=artifact_coverage())
            self.assertEqual(ResultArtifact.resume(root, artifact_identity()), receipt)

    def test_exact_checkpoint_65535_65536_65537_byte_boundaries(self) -> None:
        baseline = DeterministicFilesystemAdapter(Path("/deterministic/model"))
        root, identity = Path("/caller/artifact"), artifact_identity()
        empty_context = prepare_completed_partition(
            kind="universe",
            exchange="SSE",
            shard_index=0,
            schema_version="context@1.0",
            records=({"notes": ""},),
        )
        ResultArtifact.create(
            root, identity, initial_context=empty_context, filesystem=baseline
        )
        overhead = len(baseline.read_bytes(root, "checkpoint.json"))
        for target in (65_535, 65_536, 65_537):
            with self.subTest(target=target):
                filesystem = DeterministicFilesystemAdapter(
                    Path("/deterministic/model"), identity_start=(1 << 64) - 1000
                )
                prepared = prepare_completed_partition(
                    kind="universe",
                    exchange="SSE",
                    shard_index=0,
                    schema_version="context@1.0",
                    records=({"notes": "p" * (target - overhead)},),
                )
                if target > 65_536:
                    with self.assertRaises(ValueError):
                        ResultArtifact.create(
                            root,
                            identity,
                            initial_context=prepared,
                            filesystem=filesystem,
                        )
                    self.assertFalse(filesystem.exists(root))
                else:
                    handle = ResultArtifact.create(
                        root, identity, initial_context=prepared, filesystem=filesystem
                    )
                    self.assertEqual(
                        len(filesystem.read_bytes(root, "checkpoint.json")), target
                    )
                    before = filesystem.inventory(root)
                    with self.assertRaises(ValueError):
                        handle.write_completed_partition(partition())
                    self.assertEqual(filesystem.inventory(root), before)
                    with self.assertRaises(ValueError):
                        ResultArtifact.checkpoint_capacity(
                            identity,
                            partition_count=9,
                            candidate_count=20,
                            initial_context=prepared,
                        )

    def test_maximum_real_identity_width_remains_resumable_in_each_future_state(
        self,
    ) -> None:
        for phase in (
            "after_journal_publication",
            "after_final_publication",
            "after_checkpoint_replacement",
            "after_stage_cleanup_item",
        ):
            with self.subTest(phase=phase):
                filesystem = DeterministicFilesystemAdapter(
                    Path("/deterministic/model"), identity_start=(1 << 64) - 1000
                )
                root, identity = Path("/caller/artifact"), artifact_identity()
                handle = ResultArtifact.create(
                    root,
                    identity,
                    initial_context=frozen_context(),
                    filesystem=filesystem,
                )
                filesystem.interrupt_next(phase)
                with self.assertRaises(KeyboardInterrupt):
                    handle.write_completed_partition(partition())
                restored = ResultArtifact.resume(root, identity, filesystem=filesystem)
                self.assertEqual(
                    restored.checkpoint()["frozen_context"]["screen_date"], "2026-10-12"
                )
                self.assertEqual(
                    restored.finalize(coverage=artifact_coverage())["file_count"], 1
                )


class ResultArtifactPreparationAbortTests(_IsolatedArtifactTest):
    run_process = ResultArtifactProductionRecoveryTests.run_process
    assert_process_ok = ResultArtifactProductionRecoveryTests.assert_process_ok

    def test_each_write_preparation_boundary_aborts_without_artifact_or_external_adoption(
        self,
    ) -> None:
        phases = (
            "after_stage_directory_created",
            "after_stage_file_created",
            "after_stage_data_written",
            "after_stage_checkpoint_written",
            "after_stage_journal_written",
            "after_stage_prepared",
        )
        for phase in phases:
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temporary:
                parent = Path(temporary).resolve()
                root, identity = parent / "artifact", artifact_identity()
                handle = ResultArtifact.create(
                    root, identity, initial_context=frozen_context()
                )
                original = filesystem_tree(root)
                crashed = self.run_process(root, "write", phase)
                self.assertEqual(crashed.returncode, 77, crashed.stderr)
                self.assertEqual(filesystem_tree(root), original)
                external = tuple(
                    (p, filesystem_tree(p))
                    for p in parent.glob(".result-artifact-*.stage")
                )
                self.assertTrue(external)
                all_before = filesystem_tree(parent)
                resumed = self.run_process(root, "resume")
                self.assert_process_ok(resumed)
                self.assertEqual(filesystem_tree(parent), all_before)
                handle.write_completed_partition(partition())
                self.assertEqual(len(handle.checkpoint()["completed_files"]), 1)
                self.assertEqual(
                    handle.finalize(coverage=artifact_coverage())["file_count"], 1
                )
                for staged, before in external:
                    self.assertEqual(filesystem_tree(staged), before)

    def test_each_initial_preparation_boundary_can_retry_create_without_adopting_staging(
        self,
    ) -> None:
        for phase in (
            "after_create_stage_directory_created",
            "after_create_checkpoint_written",
            "after_create_owner_written",
            "after_create_prepared",
        ):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temporary:
                parent, identity = Path(temporary).resolve(), artifact_identity()
                root = parent / "artifact"
                crashed = self.run_process(root, "create", phase)
                self.assertEqual(crashed.returncode, 77, crashed.stderr)
                self.assertFalse(root.exists())
                external = tuple(
                    (p, filesystem_tree(p))
                    for p in parent.glob(".result-artifact-create-*.stage")
                )
                self.assertTrue(external)
                created = ResultArtifact.create(root, identity)
                self.assertEqual(created.checkpoint()["completed_files"], [])
                for staged, before in external:
                    self.assertEqual(filesystem_tree(staged), before)
                    self.assertNotEqual(root.stat().st_ino, staged.stat().st_ino)

    def test_initial_abort_never_adopts_a_later_caller_target(self) -> None:
        from a_share_research.result_artifact import ProductionFilesystemAdapter

        with tempfile.TemporaryDirectory() as temporary:
            root, identity = Path(temporary).resolve() / "artifact", artifact_identity()
            caller_state = []

            class ExternalTargetAdapter(ProductionFilesystemAdapter):
                def _phase(self, phase):
                    if phase == "after_create_prepared":
                        root.mkdir()
                        (root / "caller-file").write_bytes(b"caller-owned")
                        caller_state.append(filesystem_tree(root))

            with self.assertRaises(ValueError):
                ResultArtifact.create(
                    root, identity, filesystem=ExternalTargetAdapter()
                )
            self.assertEqual(filesystem_tree(root), caller_state[0])
            with self.assertRaises(ValueError):
                ResultArtifact.resume(root, identity)
            self.assertEqual(filesystem_tree(root), caller_state[0])


class ResultArtifactReadTests(_IsolatedArtifactTest):
    def test_only_completed_files_are_read_through_a_validated_transaction(
        self,
    ) -> None:
        filesystem = DeterministicFilesystemAdapter(Path("/deterministic/model"))
        root, identity = Path("/caller/artifact"), artifact_identity()
        handle = ResultArtifact.create(root, identity, filesystem=filesystem)
        with self.assertRaises(ValueError):
            handle.read_completed_file(partition().relative_path)
        handle.write_completed_partition(partition())
        before = filesystem.inventory(root)
        self.assertEqual(
            handle.read_completed_file(partition().relative_path),
            partition().content_bytes,
        )
        self.assertEqual(filesystem.inventory(root), before)
        for unknown in (
            "../checkpoint.json",
            "checkpoint.json",
            ".protocol/unknown",
            "partitions/universe/SSE/part-00001.jsonl",
        ):
            with self.subTest(unknown=unknown), self.assertRaises(ValueError):
                handle.read_completed_file(unknown)
        filesystem.tamper(root, "unknown", b"outside")
        before = filesystem.inventory(root)
        with self.assertRaises(ValueError):
            handle.read_completed_file(partition().relative_path)
        self.assertEqual(filesystem.inventory(root), before)


class ResultArtifactConcurrencyTests(_IsolatedArtifactTest):
    def test_two_live_handles_can_write_concurrently_without_losing_a_shard(
        self,
    ) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        with tempfile.TemporaryDirectory() as temporary:
            root, identity = Path(temporary).resolve() / "artifact", artifact_identity()
            first = ResultArtifact.create(root, identity)
            second = ResultArtifact.resume(root, identity)
            barrier = Barrier(2)

            def write(handle, index):
                barrier.wait(timeout=5)
                handle.write_completed_partition(partition(index))

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(write, first, 0), pool.submit(write, second, 1)]
                for future in futures:
                    future.result(timeout=10)
            self.assertEqual(
                first.finalize(coverage=artifact_coverage())["file_count"], 2
            )

    def test_independent_processes_racing_exclusive_directory_publication_have_one_winner(
        self,
    ) -> None:
        import os
        import subprocess
        import sys

        worker = r"""
import os, sys, time
from pathlib import Path
from tests.test_result_artifact import artifact_identity
from a_share_research.result_artifact import ProductionFilesystemAdapter, ResultArtifact
class BarrierAdapter(ProductionFilesystemAdapter):
    def _phase(self, phase):
        if phase == "after_create_prepared":
            ready = Path(sys.argv[2])
            (ready / str(os.getpid())).write_text("ready")
            end = time.monotonic() + 5
            while len(list(ready.iterdir())) != 2:
                if time.monotonic() > end:
                    raise RuntimeError("create barrier timed out")
                time.sleep(0.005)
try:
    ResultArtifact.create(Path(sys.argv[1]), artifact_identity(), filesystem=BarrierAdapter(anchor_root=Path(sys.argv[3])))
    print("winner")
except ValueError:
    print("refused")
"""
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            root, ready = parent / "artifact", parent / "ready"
            ready.mkdir()
            processes = [
                subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        worker,
                        str(root),
                        str(ready),
                        str(self.anchor_root),
                    ],
                    cwd=Path(__file__).resolve().parents[1],
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for _ in range(2)
            ]
            outputs = []
            for process in processes:
                out, err = process.communicate(timeout=15)
                self.assertEqual(process.returncode, 0, err)
                outputs.append(out.strip())
            self.assertCountEqual(outputs, ["winner", "refused"])
            self.assertEqual(
                ResultArtifact.resume(root, artifact_identity()).checkpoint()[
                    "completed_files"
                ],
                [],
            )

    def test_write_and_finalize_serialize_and_old_handles_remain_sealed(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        with tempfile.TemporaryDirectory() as temporary:
            root, identity = Path(temporary).resolve() / "artifact", artifact_identity()
            writer = ResultArtifact.create(root, identity)
            finalizer = ResultArtifact.resume(root, identity)
            barrier = Barrier(2)

            def write():
                barrier.wait(timeout=5)
                try:
                    writer.write_completed_partition(partition())
                    return True
                except ValueError:
                    return False

            def finalize():
                barrier.wait(timeout=5)
                return finalizer.finalize(coverage=artifact_coverage())

            with ThreadPoolExecutor(max_workers=2) as pool:
                write_future, finalize_future = (
                    pool.submit(write),
                    pool.submit(finalize),
                )
                wrote, receipt = (
                    write_future.result(timeout=10),
                    finalize_future.result(timeout=10),
                )
            self.assertEqual(receipt["file_count"], 1 if wrote else 0)
            self.assertEqual(ResultArtifact.resume(root, identity), receipt)
            with self.assertRaises(ValueError):
                writer.write_completed_partition(partition(1))

    def test_different_artifacts_do_not_share_a_transaction_lock(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        from a_share_research.result_artifact import ProductionFilesystemAdapter

        entered, release = Event(), Event()

        class PausedAdapter(ProductionFilesystemAdapter):
            armed = False

            def _phase(self, phase):
                if self.armed and phase == "before_apply":
                    self.armed = False
                    entered.set()
                    if not release.wait(timeout=5):
                        raise RuntimeError("artifact lock pause timed out")

        with tempfile.TemporaryDirectory() as temporary:
            parent, identity = Path(temporary).resolve(), artifact_identity()
            adapter = PausedAdapter()
            first = ResultArtifact.create(
                parent / "first", identity, filesystem=adapter
            )
            second = ResultArtifact.create(parent / "second", identity)
            adapter.armed = True
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(first.write_completed_partition, partition())
                self.assertTrue(entered.wait(timeout=5))
                try:
                    second.write_completed_partition(partition())
                    self.assertEqual(
                        second.finalize(coverage=artifact_coverage())["file_count"], 1
                    )
                finally:
                    release.set()
                future.result(timeout=10)

    def test_hostile_equality_and_union_can_only_mutate_detached_checkpoint(
        self,
    ) -> None:
        filesystem = DeterministicFilesystemAdapter(Path("/deterministic/model"))
        root, identity = Path("/caller/artifact"), artifact_identity()
        handle = ResultArtifact.create(
            root, identity, initial_context=frozen_context(), filesystem=filesystem
        )
        original = filesystem.inventory(root)

        class Counterpart:
            def __eq__(self, value):
                dict.clear(value)
                return False

            def __ror__(self, value):
                dict.__setitem__(value, "outside", "modified")
                return value

        detached = handle.checkpoint()
        self.assertFalse(detached == Counterpart())
        self.assertEqual(detached, {})
        handle.checkpoint() | Counterpart()
        self.assertEqual(filesystem.inventory(root), original)
        self.assertEqual(
            handle.checkpoint()["frozen_context"]["research_now"],
            "2026-10-13T10:00:00+08:00",
        )


class ResultArtifactAdversarialFilesystemTests(_IsolatedArtifactTest):
    run_process = ResultArtifactProductionRecoveryTests.run_process
    assert_process_ok = ResultArtifactProductionRecoveryTests.assert_process_ok

    def test_owned_stage_plus_unknown_or_same_byte_replacement_refuses_without_cleanup(
        self,
    ) -> None:
        for change in ("unknown", "file", "directory"):
            with (
                self.subTest(change=change),
                tempfile.TemporaryDirectory() as temporary,
            ):
                parent, identity = Path(temporary).resolve(), artifact_identity()
                root = parent / "artifact"
                ResultArtifact.create(root, identity)
                crash = self.run_process(root, "write", "after_journal_publication")
                self.assertEqual(crash.returncode, 77, crash.stderr)
                stage = next(parent.glob(".result-artifact-*.stage"))
                if change == "unknown":
                    (stage / "zz-unknown").write_bytes(b"caller-owned")
                elif change == "file":
                    target = stage / "data"
                    replacement = parent / "same-bytes"
                    replacement.write_bytes(target.read_bytes())
                    replacement.replace(target)
                else:
                    moved = parent / "moved-stage"
                    stage.rename(moved)
                    stage.mkdir()
                    for source in moved.iterdir():
                        (stage / source.name).write_bytes(source.read_bytes())
                before = filesystem_tree(parent)
                with self.assertRaises(ValueError):
                    ResultArtifact.resume(root, identity)
                self.assertEqual(filesystem_tree(parent), before)

    def test_root_ancestor_and_fixed_directory_replacements_fail_closed(self) -> None:
        import shutil

        for change in ("root", "ancestor", "fixed-directory"):
            with (
                self.subTest(change=change),
                tempfile.TemporaryDirectory() as temporary,
            ):
                parent, identity = Path(temporary).resolve(), artifact_identity()
                ancestor = parent / "ancestor"
                ancestor.mkdir()
                root = ancestor / "artifact"
                handle = ResultArtifact.create(root, identity)
                if change == "root":
                    root.rename(ancestor / "old-root")
                    shutil.copytree(ancestor / "old-root", root)
                elif change == "ancestor":
                    ancestor.rename(parent / "old-ancestor")
                    ancestor.symlink_to(
                        parent / "old-ancestor", target_is_directory=True
                    )
                else:
                    target = root / "partitions" / "universe" / "SSE"
                    target.rmdir()
                    target.mkdir()
                before = filesystem_tree(parent)
                with self.assertRaises(ValueError):
                    handle.write_completed_partition(partition())
                self.assertEqual(filesystem_tree(parent), before)

    def test_deleted_journal_and_forged_hardlink_are_unowned(self) -> None:
        import os

        for change in ("deleted", "hardlink"):
            with (
                self.subTest(change=change),
                tempfile.TemporaryDirectory() as temporary,
            ):
                parent, identity = Path(temporary).resolve(), artifact_identity()
                root = parent / "artifact"
                handle = ResultArtifact.create(root, identity)
                handle.write_completed_partition(partition())
                handle.finalize(coverage=artifact_coverage())
                journal = next((root / ".protocol").glob("txn-*.json"))
                if change == "deleted":
                    journal.unlink()
                else:
                    os.link(journal, root / "forged-manifest-residue")
                before = filesystem_tree(root)
                with self.assertRaises(ValueError):
                    ResultArtifact.resume(root, identity)
                self.assertEqual(filesystem_tree(root), before)

    def test_multi_entry_cleanup_plan_validates_last_entry_before_removing_first(
        self,
    ) -> None:
        from a_share_research.result_artifact import ProductionFilesystemAdapter

        with tempfile.TemporaryDirectory() as temporary:
            parent, identity = Path(temporary).resolve(), artifact_identity()
            root = parent / "artifact"
            held = []

            class HeldStageAdapter(ProductionFilesystemAdapter):
                holding = False

                def _phase(self, phase):
                    if self.holding and phase == "after_anchor_cleanup_closure":
                        self.holding = False
                        stage = next((self._anchor_path() / "quarantine").iterdir())
                        destination = self._anchor_path() / f"held-owned-{len(held)}"
                        held.append((stage, destination))
                        stage.rename(destination)
                        raise KeyboardInterrupt(
                            "closed cleanup residue held by fixture"
                        )

            adapter = HeldStageAdapter(anchor_root=self.anchor_root)
            handle = ResultArtifact.create(root, identity, filesystem=adapter)
            for index in range(2):
                adapter.holding = True
                with self.assertRaises(KeyboardInterrupt):
                    handle.write_completed_partition(partition(index))
            # Restore exact closed quarantine residues from two transactions.
            # This trusted fixture does not invent records or copy identities.
            for stage, destination in held:
                destination.rename(stage)
            unknown = held[-1][0] / "zz-unknown"
            unknown.write_bytes(b"caller-owned")
            before = filesystem_tree(parent)
            with self.assertRaises(ValueError):
                ResultArtifact.resume(root, identity)
            self.assertEqual(filesystem_tree(parent), before)
            self.assertTrue(held[0][0].exists())
            unknown.unlink()  # The external fixture removes its own test object.
            resumed = ResultArtifact.resume(root, identity)
            self.assertEqual(len(resumed.checkpoint()["completed_files"]), 2)
            self.assertTrue(all(not stage.exists() for stage, _ in held))
            self.assertEqual(
                resumed.finalize(coverage=artifact_coverage())["file_count"], 2
            )


class ResultArtifactAncestorGenerationTests(_IsolatedArtifactTest):
    def test_moved_original_root_inside_replaced_ancestor_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent, identity = Path(temporary).resolve(), artifact_identity()
            ancestor = parent / "ancestor"
            ancestor.mkdir()
            root = ancestor / "artifact"
            handle = ResultArtifact.create(root, identity)
            ancestor.rename(parent / "old-ancestor")
            ancestor.mkdir()
            (parent / "old-ancestor" / "artifact").rename(root)
            before = filesystem_tree(parent)
            with self.assertRaises(ValueError):
                handle.write_completed_partition(partition())
            self.assertEqual(filesystem_tree(parent), before)


class ResultArtifactIntegrationTests(_IsolatedArtifactTest):
    def test_prepared_authority_is_consumed_without_rereading_caller_mappings(
        self,
    ) -> None:
        from collections.abc import Mapping

        class OnePass(Mapping):
            def __init__(self, value):
                self.value, self.traversals = value, 0

            def __getitem__(self, key):
                raise AssertionError("caller mapping was reread")

            def __len__(self):
                raise AssertionError("caller mapping length was reread")

            def __iter__(self):
                raise AssertionError("caller mapping was iterated twice")

            def items(self):
                self.traversals += 1
                if self.traversals != 1:
                    raise AssertionError("caller mapping was observed twice")
                return iter(tuple(self.value.items()))

        caller_context = OnePass({"research_now": "2026-10-13T10:00:00+08:00"})
        caller_partition = OnePass({"canonical_security_id": "SSE:600519"})
        caller_candidate = OnePass({"status": "limited"})
        prepared_context = prepare_completed_partition(
            kind="universe",
            exchange="SSE",
            shard_index=0,
            schema_version="context@1.0",
            records=(caller_context,),
        )
        prepared_partition = prepare_completed_partition(
            kind="universe",
            exchange="SSE",
            shard_index=0,
            schema_version="1.0",
            records=(caller_partition,),
        )
        prepared_candidate = prepare_candidate(
            canonical_security_id="SSE:600519",
            schema_version="1.0",
            document=caller_candidate,
        )
        for caller in (caller_context, caller_partition, caller_candidate):
            caller.value.clear()
        filesystem = DeterministicFilesystemAdapter(Path("/deterministic/model"))
        root, identity = Path("/caller/artifact"), artifact_identity()
        handle = ResultArtifact.create(
            root, identity, initial_context=prepared_context, filesystem=filesystem
        )
        handle.write_completed_partition(prepared_partition)
        handle.write_candidate(prepared_candidate)
        self.assertEqual(
            handle.checkpoint()["frozen_context"]["research_now"],
            "2026-10-13T10:00:00+08:00",
        )
        self.assertEqual(handle.finalize(coverage=artifact_coverage())["file_count"], 2)
        self.assertTrue(
            all(
                caller.traversals == 1
                for caller in (caller_context, caller_partition, caller_candidate)
            )
        )

    def test_copied_installed_module_completes_and_recovers_bounded_receipt_without_path_leak(
        self,
    ) -> None:
        import os
        import shutil
        import subprocess
        import sys

        source_skill = (
            Path(__file__).resolve().parents[1] / "skill" / "a-share-research"
        )
        worker = r"""
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import a_share_research.result_artifact as module
from a_share_research.result_artifact import ResultArtifact, ResultArtifactIdentity
from a_share_research.result_artifact_inputs import prepare_completed_partition, prepare_candidate
assert Path(module.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())
identity = ResultArtifactIdentity("1.0", b"request", b"producer", b"plan", b"operations")
context = prepare_completed_partition(kind="universe", exchange="SSE", shard_index=0,
    schema_version="context@1.0", records=({"research_now":"2026-10-13T10:00:00+08:00"},))
root = Path(sys.argv[2])
artifact = ResultArtifact.create(root, identity, initial_context=context)
artifact.write_completed_partition(prepare_completed_partition(kind="universe", exchange="SSE", shard_index=0,
    schema_version="1.0", records=({"canonical_security_id":"SSE:600519"},)))
artifact.write_candidate(prepare_candidate(canonical_security_id="SSE:600519", schema_version="1.0", document={"status":"limited"}))
coverage = {exchange:{key:0 for key in ("acquired","expected","failed","ranked","resolved","unranked","unsupported")}
    for exchange in ("BSE","SSE","SZSE")}
receipt = artifact.finalize(coverage=coverage)
assert ResultArtifact.resume(root, identity) == receipt
ResultArtifact.validate_receipt(root, identity, receipt)
print(json.dumps(receipt, sort_keys=True))
"""
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            installed = parent / "installed-skill"
            packaging_source = parent / "packaging-source"
            excluded = shutil.ignore_patterns(
                ".state", ".runtime", "__pycache__", "*.pyc"
            )
            shutil.copytree(source_skill, packaging_source, ignore=excluded)
            (packaging_source / ".state").mkdir()
            (packaging_source / ".state" / "unrelated-authority").write_bytes(
                b"another installation's state"
            )
            shutil.copytree(packaging_source, installed, ignore=excluded)
            self.assertFalse((installed / ".state").exists())
            root = parent / "caller-owned-artifact"
            result = subprocess.run(
                [sys.executable, "-c", worker, str(installed / "scripts"), str(root)],
                cwd=parent,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["file_count"], 2)
            self.assertLessEqual(len(result.stdout.encode()), 1024)
            self.assertNotIn(str(root), result.stdout)
            self.assertNotIn(str(installed), result.stdout)
            self.assertTrue(
                (installed / ".state" / "result-artifact-anchors" / "v1").is_dir()
            )
            for path in (*root.rglob("*"), *(installed / ".state").rglob("*")):
                if path.is_file():
                    self.assertNotIn(str(root).encode(), path.read_bytes())
                    self.assertNotIn(str(installed).encode(), path.read_bytes())


class ResultArtifactExceptionMatrixTests(_IsolatedArtifactTest):
    def test_ordinary_io_and_keyboard_interrupt_use_the_same_recoverable_state_contract(
        self,
    ) -> None:
        preparation = (
            "before_apply",
            "after_stage_directory_created",
            "after_stage_file_created",
            "after_stage_data_written",
            "after_stage_checkpoint_written",
            "after_stage_journal_written",
            "after_stage_prepared",
            "before_file_fsync",
            "after_file_fsync",
            "before_directory_fsync",
            "after_directory_fsync",
        )
        publication = (
            "after_journal_publication",
            "after_final_publication",
            "after_checkpoint_replacement",
            "after_stage_cleanup_item",
            "after_stage_cleanup",
        )
        for exception_type in (OSError, KeyboardInterrupt):
            for phase in (*preparation, *publication):
                with self.subTest(exception=exception_type.__name__, phase=phase):
                    filesystem = DeterministicFilesystemAdapter(
                        Path("/deterministic/model")
                    )
                    root, identity = Path("/caller/artifact"), artifact_identity()
                    handle = ResultArtifact.create(
                        root,
                        identity,
                        initial_context=frozen_context(),
                        filesystem=filesystem,
                    )
                    before = filesystem.inventory(root)
                    filesystem.fail_next(
                        phase, exception_type("private path must not leak")
                    )
                    with self.assertRaises(
                        ValueError if exception_type is OSError else KeyboardInterrupt
                    ):
                        handle.write_completed_partition(partition())
                    restored = ResultArtifact.resume(
                        root, identity, filesystem=filesystem
                    )
                    if phase in preparation:
                        self.assertEqual(filesystem.inventory(root), before)
                        restored.write_completed_partition(partition())
                    else:
                        with self.assertRaises(ValueError):
                            restored.write_completed_partition(partition())
                    self.assertEqual(
                        restored.finalize(coverage=artifact_coverage())["file_count"], 1
                    )

    def test_initial_io_and_interrupt_failures_preserve_exclusive_create_recovery(
        self,
    ) -> None:
        for exception_type in (OSError, KeyboardInterrupt):
            for phase in (
                "after_create_stage_directory_created",
                "after_create_checkpoint_written",
                "after_create_owner_written",
                "after_create_prepared",
                "after_create_publication",
            ):
                with self.subTest(exception=exception_type.__name__, phase=phase):
                    filesystem = DeterministicFilesystemAdapter(
                        Path("/deterministic/model")
                    )
                    root, identity = Path("/caller/artifact"), artifact_identity()
                    filesystem.fail_next(phase, exception_type("interruption"))
                    with self.assertRaises(
                        ValueError if exception_type is OSError else KeyboardInterrupt
                    ):
                        ResultArtifact.create(
                            root,
                            identity,
                            initial_context=frozen_context(),
                            filesystem=filesystem,
                        )
                    if phase == "after_create_publication":
                        restored = ResultArtifact.resume(
                            root, identity, filesystem=filesystem
                        )
                    else:
                        self.assertFalse(filesystem.exists(root))
                        restored = ResultArtifact.create(
                            root,
                            identity,
                            initial_context=frozen_context(),
                            filesystem=filesystem,
                        )
                    self.assertEqual(restored.checkpoint()["completed_files"], [])
                    self.assertEqual(
                        restored.checkpoint()["frozen_context"]["screen_date"],
                        "2026-10-12",
                    )


class ResultArtifactPublicationRevalidationTests(_IsolatedArtifactTest):
    def test_prepared_create_descriptor_name_and_complete_inventory_are_revalidated(
        self,
    ) -> None:
        from a_share_research.result_artifact import ProductionFilesystemAdapter

        for change in ("directory", "unknown-entry", "checkpoint"):
            with (
                self.subTest(change=change),
                tempfile.TemporaryDirectory() as temporary,
            ):
                parent, identity = Path(temporary).resolve(), artifact_identity()
                target = parent / "artifact"
                before = []

                class ReplacedRootAdapter(ProductionFilesystemAdapter):
                    def _phase(self, phase):
                        if phase == "after_create_prepared":
                            stage = next(parent.glob(".result-artifact-create-*.stage"))
                            if change == "directory":
                                stage.rename(parent / "original-prepared-root")
                                stage.mkdir()
                                (stage / "caller-file").write_bytes(b"caller-owned")
                            elif change == "unknown-entry":
                                (stage / "caller-file").write_bytes(b"caller-owned")
                            else:
                                (stage / "checkpoint.json").write_bytes(b"caller-owned")
                            before.append(filesystem_tree(parent))

                with self.assertRaises(ValueError):
                    ResultArtifact.create(
                        target,
                        identity,
                        initial_context=frozen_context(),
                        filesystem=ReplacedRootAdapter(),
                    )
                self.assertFalse(target.exists())
                self.assertEqual(filesystem_tree(parent), before[0])

    def test_prepared_write_checks_every_staged_object_before_journal_publication(
        self,
    ) -> None:
        from a_share_research.result_artifact import ProductionFilesystemAdapter

        for change in ("data", "journal-inode", "unknown-entry"):
            with (
                self.subTest(change=change),
                tempfile.TemporaryDirectory() as temporary,
            ):
                parent, identity = Path(temporary).resolve(), artifact_identity()
                root = parent / "artifact"

                class ReplacedStageAdapter(ProductionFilesystemAdapter):
                    def _phase(self, phase):
                        if phase == "after_stage_prepared":
                            stage = next(parent.glob(".result-artifact-*.stage"))
                            if change == "data":
                                (stage / "data").write_bytes(b"caller-owned")
                            elif change == "journal-inode":
                                replacement = parent / "replacement"
                                replacement.write_bytes(
                                    (stage / "journal").read_bytes()
                                )
                                replacement.replace(stage / "journal")
                            else:
                                (stage / "caller-file").write_bytes(b"caller-owned")

                handle = ResultArtifact.create(
                    root, identity, filesystem=ReplacedStageAdapter()
                )
                before = filesystem_tree(root)
                with self.assertRaises(ValueError):
                    handle.write_completed_partition(partition())
                self.assertEqual(filesystem_tree(root), before)

    def test_sealed_recovery_rechecks_detectable_mutation_during_durability(
        self,
    ) -> None:
        from a_share_research.result_artifact import ProductionFilesystemAdapter

        with tempfile.TemporaryDirectory() as temporary:
            root, identity = Path(temporary).resolve() / "artifact", artifact_identity()
            handle = ResultArtifact.create(root, identity)
            handle.write_completed_partition(partition())
            receipt = handle.finalize(coverage=artifact_coverage())

            class MutatingSyncAdapter(ProductionFilesystemAdapter):
                changed = False

                def _phase(self, phase):
                    if phase == "before_file_fsync" and not self.changed:
                        self.changed = True
                        target = root / partition().relative_path
                        target.write_bytes(
                            target.read_bytes().replace(b"1.20", b"9.99")
                        )

            with self.assertRaises(ValueError):
                ResultArtifact.resume(root, identity, filesystem=MutatingSyncAdapter())
            with self.assertRaises(ValueError):
                ResultArtifact.validate_receipt(root, identity, receipt)


class ResultArtifactInitialStageBindingTests(_IsolatedArtifactTest):
    def test_created_stage_is_bound_before_initialization_writes(self) -> None:
        from a_share_research.result_artifact import ProductionFilesystemAdapter

        for action in ("create", "write"):
            with (
                self.subTest(action=action),
                tempfile.TemporaryDirectory() as temporary,
            ):
                parent, identity = Path(temporary).resolve(), artifact_identity()
                root = parent / "artifact"
                before = []

                class ReplacedEmptyStageAdapter(ProductionFilesystemAdapter):
                    def _phase(self, phase):
                        expected = (
                            "after_create_stage_directory_created"
                            if action == "create"
                            else "after_stage_directory_created"
                        )
                        if phase == expected:
                            stage = next(parent.glob(".result-artifact-*.stage"))
                            stage.rename(parent / "original-empty-stage")
                            stage.mkdir()
                            (stage / "caller-file").write_bytes(b"caller-owned")
                            before.append(filesystem_tree(parent))

                if action == "write":
                    handle = ResultArtifact.create(
                        root, identity, filesystem=ReplacedEmptyStageAdapter()
                    )
                with self.assertRaises(ValueError):
                    if action == "create":
                        ResultArtifact.create(
                            root, identity, filesystem=ReplacedEmptyStageAdapter()
                        )
                    else:
                        handle.write_completed_partition(partition())
                self.assertEqual(filesystem_tree(parent), before[0])


class ResultArtifactPostpublicationOwnershipTests(_IsolatedArtifactTest):
    def test_create_returns_no_handle_after_target_ownership_changes(self) -> None:
        from a_share_research.result_artifact import ProductionFilesystemAdapter

        with tempfile.TemporaryDirectory() as temporary:
            parent, identity = Path(temporary).resolve(), artifact_identity()
            target = parent / "artifact"
            before = []

            class ChangedTargetAdapter(ProductionFilesystemAdapter):
                def _phase(self, phase):
                    if phase == "after_create_publication":
                        target.rename(parent / "original-published-root")
                        target.mkdir()
                        (target / "caller-file").write_bytes(b"caller-owned")
                        before.append(filesystem_tree(parent))

            with self.assertRaises(ValueError):
                ResultArtifact.create(
                    target,
                    identity,
                    initial_context=frozen_context(),
                    filesystem=ChangedTargetAdapter(),
                )
            self.assertEqual(filesystem_tree(parent), before[0])
            self.assertEqual((target / "caller-file").read_bytes(), b"caller-owned")


class ResultArtifactCreationPlanTests(_IsolatedArtifactTest):
    def test_each_create_phase_is_checked_against_original_plan(self) -> None:
        from a_share_research.result_artifact import ProductionFilesystemAdapter

        for phase in (
            "after_create_checkpoint_written",
            "after_create_owner_written",
            "after_create_prepared",
            "after_create_publication",
        ):
            for change in (
                "unknown",
                "checkpoint",
                "checkpoint-inode",
                "directory",
                *(
                    ("owner", "owner-inode")
                    if phase != "after_create_checkpoint_written"
                    else ()
                ),
            ):
                with (
                    self.subTest(phase=phase, change=change),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    parent, identity = Path(temporary).resolve(), artifact_identity()
                    root = parent / "artifact"
                    before = []

                    class ChangedCreationPlan(ProductionFilesystemAdapter):
                        def _phase(self, actual_phase):
                            if actual_phase == phase:
                                stage = (
                                    root
                                    if phase == "after_create_publication"
                                    else next(
                                        parent.glob(".result-artifact-create-*.stage")
                                    )
                                )
                                if change == "unknown":
                                    (stage / "foreign").write_bytes(b"caller-owned")
                                elif change == "checkpoint":
                                    (stage / "checkpoint.json").write_bytes(b"{}\n")
                                elif change == "owner":
                                    next(
                                        (stage / ".protocol").glob("create-*.json")
                                    ).write_bytes(b"{}\n")
                                elif change.endswith("-inode"):
                                    destination = (
                                        stage / "checkpoint.json"
                                        if change == "checkpoint-inode"
                                        else next(
                                            (stage / ".protocol").glob("create-*.json")
                                        )
                                    )
                                    replacement = parent / "replacement"
                                    replacement.write_bytes(destination.read_bytes())
                                    replacement.replace(destination)
                                else:
                                    directory = (
                                        stage / "partitions" / "universe" / "SSE"
                                    )
                                    directory.rmdir()
                                    directory.mkdir()
                                before.append(filesystem_tree(parent))

                    with self.assertRaises(ValueError):
                        ResultArtifact.create(
                            root,
                            identity,
                            initial_context=frozen_context(),
                            filesystem=ChangedCreationPlan(),
                        )
                    self.assertEqual(root.exists(), phase == "after_create_publication")
                    self.assertEqual(filesystem_tree(parent), before[0])
                    if root.exists():
                        with self.assertRaises(ValueError):
                            ResultArtifact.resume(root, identity)
                        self.assertEqual(filesystem_tree(parent), before[0])

    def test_create_durability_phases_keep_original_bytes_and_inventory(self) -> None:
        from a_share_research.result_artifact import ProductionFilesystemAdapter

        for phase in ("before_file_fsync", "after_file_fsync"):
            for occurrence in (1, 2):
                with (
                    self.subTest(phase=phase, occurrence=occurrence),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    parent, identity = Path(temporary).resolve(), artifact_identity()
                    root = parent / "artifact"
                    before = []

                    class ChangedDuringSync(ProductionFilesystemAdapter):
                        seen = 0

                        def _phase(self, actual_phase):
                            if actual_phase == phase:
                                self.seen += 1
                                if self.seen == occurrence:
                                    stage = next(
                                        parent.glob(".result-artifact-create-*.stage")
                                    )
                                    destination = (
                                        stage / "checkpoint.json"
                                        if occurrence == 1
                                        else next(
                                            (stage / ".protocol").glob("create-*.json")
                                        )
                                    )
                                    destination.write_bytes(b"{}\n")
                                    before.append(filesystem_tree(parent))

                    with self.assertRaises(ValueError):
                        ResultArtifact.create(
                            root, identity, filesystem=ChangedDuringSync()
                        )
                    self.assertFalse(root.exists())
                    self.assertEqual(filesystem_tree(parent), before[0])

        for phase in ("before_directory_fsync", "after_directory_fsync"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temporary:
                parent, identity = Path(temporary).resolve(), artifact_identity()
                root = parent / "artifact"
                before = []

                class ChangedInventoryDuringSync(ProductionFilesystemAdapter):
                    changed = False
                    prepared = False

                    def _phase(self, actual_phase):
                        if actual_phase == "after_create_owner_written":
                            self.prepared = True
                        if actual_phase == phase and self.prepared and not self.changed:
                            self.changed = True
                            stage = next(parent.glob(".result-artifact-create-*.stage"))
                            (stage / ".protocol" / "foreign").write_bytes(
                                b"caller-owned"
                            )
                            before.append(filesystem_tree(parent))

                with self.assertRaises(ValueError):
                    ResultArtifact.create(
                        root, identity, filesystem=ChangedInventoryDuringSync()
                    )
                self.assertFalse(root.exists())
                self.assertEqual(filesystem_tree(parent), before[0])

    def test_deterministic_create_rechecks_original_published_plan(self) -> None:
        for change in ("unknown", "checkpoint", "checkpoint-inode"):
            with self.subTest(change=change):
                root, identity = Path("/caller/artifact"), artifact_identity()
                before = []

                class ChangedPublishedPlan(DeterministicFilesystemAdapter):
                    def _phase(self, phase):
                        super()._phase(phase)
                        if phase == "after_create_publication":
                            self.tamper(
                                root,
                                "foreign" if change == "unknown" else "checkpoint.json",
                                b"caller-owned"
                                if change == "unknown"
                                else self.read_bytes(root, "checkpoint.json"),
                                replace_identity=change == "checkpoint-inode",
                            )
                            if change == "checkpoint":
                                self.tamper(root, "checkpoint.json", b"{}\n")
                            before.append(self.inventory(root))

                filesystem = ChangedPublishedPlan(Path("/deterministic/model"))
                with self.assertRaises(ValueError):
                    ResultArtifact.create(root, identity, filesystem=filesystem)
                self.assertEqual(filesystem.inventory(root), before[0])
                with self.assertRaises(ValueError):
                    ResultArtifact.resume(root, identity, filesystem=filesystem)
                self.assertEqual(filesystem.inventory(root), before[0])

    def test_changed_protocol_directory_cannot_redirect_owner_writes(self) -> None:
        from a_share_research.result_artifact import ProductionFilesystemAdapter

        with tempfile.TemporaryDirectory() as temporary:
            parent, identity = Path(temporary).resolve(), artifact_identity()
            root, foreign = parent / "artifact", parent / "foreign"
            foreign.mkdir()
            (foreign / "caller-file").write_bytes(b"caller-owned")
            before = []

            class RedirectedProtocol(ProductionFilesystemAdapter):
                def _phase(self, phase):
                    if phase == "after_create_checkpoint_written":
                        stage = next(parent.glob(".result-artifact-create-*.stage"))
                        (stage / ".protocol").rmdir()
                        (stage / ".protocol").symlink_to(
                            foreign, target_is_directory=True
                        )
                        before.append(filesystem_tree(parent))

            with self.assertRaises(ValueError):
                ResultArtifact.create(root, identity, filesystem=RedirectedProtocol())
            self.assertEqual(filesystem_tree(parent), before[0])
            self.assertEqual(
                sorted(path.name for path in foreign.iterdir()), ["caller-file"]
            )

    def test_receipt_count_types_are_exact_canonical_json_integers(self) -> None:
        for records, invalid_counts in ((0, (False, 0.0)), (1, (True, 1.0))):
            with self.subTest(records=records):
                filesystem = DeterministicFilesystemAdapter(
                    Path("/deterministic/model")
                )
                root, identity = Path("/caller/artifact"), artifact_identity()
                handle = ResultArtifact.create(root, identity, filesystem=filesystem)
                if records:
                    handle.write_completed_partition(partition())
                receipt = handle.finalize(coverage=artifact_coverage())
                before = filesystem.inventory(root)
                for invalid in invalid_counts:
                    for key in ("file_count", "record_count"):
                        with self.subTest(value=invalid, key=key):
                            bad = {**receipt, key: invalid}
                            with self.assertRaises(ValueError):
                                ResultArtifact.validate_receipt(
                                    root, identity, bad, filesystem=filesystem
                                )
                            self.assertEqual(filesystem.inventory(root), before)
                ResultArtifact.validate_receipt(
                    root, identity, receipt, filesystem=filesystem
                )


class ResultArtifactProtocolTypeTests(_IsolatedArtifactTest):
    def test_journal_sequence_requires_exact_integer_in_partial_and_sealed_states(
        self,
    ) -> None:
        for sealed in (False, True):
            for sequence in range(1, 3 if sealed else 2):
                invalid_values = (
                    (True, float(sequence)) if sequence == 1 else (float(sequence),)
                )
                for invalid in invalid_values:
                    with self.subTest(
                        sealed=sealed, sequence=sequence, invalid=invalid
                    ):
                        filesystem = DeterministicFilesystemAdapter(
                            Path("/deterministic/model")
                        )
                        root, identity = Path("/caller/artifact"), artifact_identity()
                        handle = ResultArtifact.create(
                            root, identity, filesystem=filesystem
                        )
                        handle.write_completed_partition(partition())
                        receipt = (
                            handle.finalize(coverage=artifact_coverage())
                            if sealed
                            else None
                        )
                        journal = next(
                            node.relative_path
                            for node in filesystem.inventory(root).nodes
                            if node.relative_path.startswith(
                                f".protocol/txn-{sequence:06d}-"
                            )
                        )
                        payload = json.loads(filesystem.read_bytes(root, journal))
                        payload["sequence"] = invalid
                        filesystem.tamper(
                            root,
                            journal,
                            (
                                json.dumps(
                                    payload,
                                    ensure_ascii=False,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                )
                                + "\n"
                            ).encode(),
                        )
                        before = filesystem.inventory(root)
                        with self.assertRaises(ValueError):
                            ResultArtifact.resume(root, identity, filesystem=filesystem)
                        self.assertEqual(filesystem.inventory(root), before)
                        if sealed:
                            with self.assertRaises(ValueError):
                                ResultArtifact.validate_receipt(
                                    root, identity, receipt, filesystem=filesystem
                                )
                            with self.assertRaises(ValueError):
                                ResultArtifact.recover_finalized_receipt(
                                    root, identity, filesystem=filesystem
                                )
                        else:
                            with self.assertRaises(ValueError):
                                handle.checkpoint()
                            with self.assertRaises(ValueError):
                                handle.read_completed_file(partition().relative_path)
                        self.assertEqual(filesystem.inventory(root), before)

    def test_every_persisted_protocol_structure_rejects_numeric_type_substitutions(
        self,
    ) -> None:
        fields = (
            ("owner", ("root_identity", 0)),
            ("owner", ("directories", ".protocol", 0)),
            ("journal-write", ("root_identity", 0)),
            ("journal-write", ("stage_files", "journal", 0)),
            ("journal-write", ("file", "byte_count")),
            ("journal-write", ("file", "record_count")),
            ("journal-finalize", ("coverage", "SSE", "acquired")),
            ("checkpoint", ("completed_files", 0, "byte_count")),
            ("checkpoint", ("completed_files", 0, "record_count")),
            ("manifest", ("files", 0, "byte_count")),
            ("manifest", ("files", 0, "record_count")),
            ("manifest", ("totals", "file_count")),
            ("manifest", ("totals", "record_count")),
            ("manifest", ("coverage", "SSE", "acquired")),
            ("receipt", ("file_count",)),
            ("receipt", ("record_count",)),
        )
        for structure, coordinate in fields:
            for representation in ("boolean", "float"):
                with self.subTest(
                    structure=structure,
                    coordinate=coordinate,
                    representation=representation,
                ):
                    filesystem = DeterministicFilesystemAdapter(
                        Path("/deterministic/model")
                    )
                    root, identity = Path("/caller/artifact"), artifact_identity()
                    handle = ResultArtifact.create(
                        root, identity, filesystem=filesystem
                    )
                    handle.write_completed_partition(partition())
                    receipt = handle.finalize(coverage=artifact_coverage())
                    paths = {
                        "owner": next(
                            node.relative_path
                            for node in filesystem.inventory(root).nodes
                            if node.relative_path.startswith(".protocol/create-")
                        ),
                        "journal-write": next(
                            node.relative_path
                            for node in filesystem.inventory(root).nodes
                            if node.relative_path.startswith(".protocol/txn-000001-")
                        ),
                        "journal-finalize": next(
                            node.relative_path
                            for node in filesystem.inventory(root).nodes
                            if node.relative_path.startswith(".protocol/txn-000002-")
                        ),
                        "checkpoint": "checkpoint.json",
                        "manifest": "manifest.json",
                    }
                    payload = (
                        json.loads(json.dumps(receipt))
                        if structure == "receipt"
                        else json.loads(filesystem.read_bytes(root, paths[structure]))
                    )
                    cursor = payload
                    for key in coordinate[:-1]:
                        cursor = cursor[key]
                    original = cursor[coordinate[-1]]
                    numeric = (
                        int(original, 16) if isinstance(original, str) else original
                    )
                    cursor[coordinate[-1]] = (
                        bool(numeric) if representation == "boolean" else float(numeric)
                    )
                    if structure != "receipt":
                        filesystem.tamper(
                            root,
                            paths[structure],
                            (
                                json.dumps(
                                    payload,
                                    ensure_ascii=False,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                )
                                + "\n"
                            ).encode(),
                        )
                    before = filesystem.inventory(root)
                    with self.assertRaises(ValueError):
                        ResultArtifact.validate_receipt(
                            root,
                            identity,
                            payload if structure == "receipt" else receipt,
                            filesystem=filesystem,
                        )
                    if structure != "receipt":
                        with self.assertRaises(ValueError):
                            ResultArtifact.resume(root, identity, filesystem=filesystem)
                    self.assertEqual(filesystem.inventory(root), before)

    def test_tampered_pending_journal_blocks_all_recovery_mutations(self) -> None:
        from a_share_research.result_artifact import ProductionFilesystemAdapter

        for operation in ("write", "finalize"):
            sequence = 1 if operation == "write" else 2
            for invalid in (True, float(sequence)):
                with (
                    self.subTest(operation=operation, invalid=invalid),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    parent, identity = Path(temporary).resolve(), artifact_identity()
                    root = parent / "artifact"

                    class InterruptedPublication(ProductionFilesystemAdapter):
                        armed = False

                        def _phase(self, phase):
                            if phase == "after_journal_publication" and self.armed:
                                raise KeyboardInterrupt("pending publication")

                    filesystem = InterruptedPublication()
                    handle = ResultArtifact.create(
                        root, identity, filesystem=filesystem
                    )
                    if operation == "finalize":
                        handle.write_completed_partition(partition())
                    filesystem.armed = True
                    with self.assertRaises(KeyboardInterrupt):
                        if operation == "write":
                            handle.write_completed_partition(partition())
                        else:
                            handle.finalize(coverage=artifact_coverage())
                    journal = next(
                        (root / ".protocol").glob(f"txn-{sequence:06d}-*.json")
                    )
                    payload = json.loads(journal.read_bytes())
                    payload["sequence"] = invalid
                    journal.write_bytes(
                        (
                            json.dumps(
                                payload,
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            )
                            + "\n"
                        ).encode()
                    )
                    before = filesystem_tree(parent)
                    with self.assertRaises(ValueError):
                        ResultArtifact.resume(root, identity)
                    self.assertEqual(filesystem_tree(parent), before)
                    self.assertTrue(
                        next(parent.glob(".result-artifact-*.stage")).exists()
                    )
                    if operation == "write":
                        self.assertFalse((root / partition().relative_path).exists())
                    else:
                        self.assertFalse((root / "manifest.json").exists())


class ResultArtifactCreateLockTests(_IsolatedArtifactTest):
    def test_create_commit_serializes_independent_resume_write_and_finalize(
        self,
    ) -> None:
        import os
        import subprocess
        import sys
        import time

        worker = r"""
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from tests.test_result_artifact import artifact_identity, artifact_coverage, frozen_context, partition
from a_share_research.result_artifact import ProductionFilesystemAdapter, ResultArtifact
root, control, action = Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
import a_share_research.result_artifact as module
module._DEFAULT_ANCHOR_ROOT = Path(sys.argv[5])
class PausedCreate(ProductionFilesystemAdapter):
    def _phase(self, phase):
        if phase == "after_create_publication":
            (control / "published").write_text("ready")
            deadline = time.monotonic() + 10
            while not (control / "release").exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError("test publication pause expired")
                time.sleep(0.005)
if action == "create":
    ResultArtifact.create(root, artifact_identity(), initial_context=frozen_context(), filesystem=PausedCreate())
    print("created")
else:
    (control / "attempted").write_text("ready")
    handle = ResultArtifact.resume(root, artifact_identity())
    (control / "resumed").write_text("ready")
    if action == "write":
        handle.write_completed_partition(partition())
    elif action == "finalize":
        handle.finalize(coverage=artifact_coverage())
    print(action + " completed")
"""
        repository = Path(__file__).resolve().parents[1]
        for action in ("resume", "write", "finalize"):
            with (
                self.subTest(action=action),
                tempfile.TemporaryDirectory() as temporary,
            ):
                parent = Path(temporary).resolve()
                root, script = parent / "artifact", parent / "worker.py"
                script.write_text(worker)
                processes = []

                def start(mode):
                    process = subprocess.Popen(
                        [
                            sys.executable,
                            str(script),
                            str(repository),
                            str(root),
                            str(parent),
                            mode,
                            str(self.anchor_root),
                        ],
                        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                    processes.append(process)
                    return process

                def wait_marker(name):
                    deadline = time.monotonic() + 5
                    while not (parent / name).exists():
                        if time.monotonic() >= deadline:
                            self.fail(f"worker did not reach {name}")
                        time.sleep(0.005)

                creator = start("create")
                try:
                    wait_marker("published")
                    follower = start(action)
                    wait_marker("attempted")
                    deadline = time.monotonic() + 0.25
                    while (
                        not (parent / "resumed").exists()
                        and time.monotonic() < deadline
                    ):
                        time.sleep(0.005)
                    entered_before_create_completed = (parent / "resumed").exists()
                    (parent / "release").write_text("continue")
                    created = creator.communicate(timeout=5)
                    followed = follower.communicate(timeout=5)
                    self.assertFalse(entered_before_create_completed)
                    self.assertEqual(creator.returncode, 0, created)
                    self.assertEqual(follower.returncode, 0, followed)
                    resumed = ResultArtifact.resume(root, artifact_identity())
                    if action == "finalize":
                        self.assertIsInstance(resumed, dict)
                        self.assertEqual(resumed["file_count"], 0)
                    else:
                        self.assertEqual(
                            len(resumed.checkpoint()["completed_files"]),
                            1 if action == "write" else 0,
                        )
                finally:
                    (parent / "release").write_text("continue")
                    for process in processes:
                        try:
                            process.communicate(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.communicate(timeout=5)

    def test_deterministic_create_commit_uses_the_same_artifact_lock(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event

        for action in ("resume", "write", "finalize"):
            with self.subTest(action=action):
                published, release, attempted, resumed = (
                    Event(),
                    Event(),
                    Event(),
                    Event(),
                )

                class PausedCreate(DeterministicFilesystemAdapter):
                    def _phase(self, phase):
                        super()._phase(phase)
                        if phase == "after_create_publication":
                            published.set()
                            if not release.wait(timeout=5):
                                raise RuntimeError("test publication pause expired")

                filesystem = PausedCreate(Path("/deterministic/model"))
                root, identity = Path("/caller/artifact"), artifact_identity()

                def follow():
                    attempted.set()
                    handle = ResultArtifact.resume(
                        root, identity, filesystem=filesystem
                    )
                    resumed.set()
                    if action == "write":
                        handle.write_completed_partition(partition())
                    elif action == "finalize":
                        handle.finalize(coverage=artifact_coverage())

                with ThreadPoolExecutor(max_workers=2) as pool:
                    creator = pool.submit(
                        ResultArtifact.create,
                        root,
                        identity,
                        initial_context=frozen_context(),
                        filesystem=filesystem,
                    )
                    self.assertTrue(published.wait(timeout=5))
                    follower = pool.submit(follow)
                    self.assertTrue(attempted.wait(timeout=5))
                    try:
                        entered_before_create_completed = resumed.wait(timeout=0.1)
                    finally:
                        release.set()
                    creator.result(timeout=5)
                    follower.result(timeout=5)
                    self.assertFalse(entered_before_create_completed)
                final = ResultArtifact.resume(root, identity, filesystem=filesystem)
                if action == "finalize":
                    self.assertIsInstance(final, dict)
                else:
                    self.assertEqual(
                        len(final.checkpoint()["completed_files"]),
                        1 if action == "write" else 0,
                    )

    def test_paused_create_does_not_lock_another_artifact(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        from a_share_research.result_artifact import ProductionFilesystemAdapter

        for backend in (ProductionFilesystemAdapter, DeterministicFilesystemAdapter):
            with (
                self.subTest(backend=backend.__name__),
                tempfile.TemporaryDirectory() as temporary,
            ):
                parent, identity = Path(temporary).resolve(), artifact_identity()
                published, release = Event(), Event()

                class PausedCreate(backend):
                    paused = False

                    def _phase(self, phase):
                        super()._phase(phase)
                        if phase == "after_create_publication" and not self.paused:
                            self.paused = True
                            published.set()
                            if not release.wait(timeout=5):
                                raise RuntimeError("test publication pause expired")

                filesystem = (
                    PausedCreate(parent)
                    if backend is DeterministicFilesystemAdapter
                    else PausedCreate()
                )

                def other_artifact():
                    other = ResultArtifact.create(
                        parent / "second",
                        identity,
                        initial_context=frozen_context(),
                        filesystem=filesystem,
                    )
                    other.write_completed_partition(partition())
                    return other.finalize(coverage=artifact_coverage())

                with ThreadPoolExecutor(max_workers=2) as pool:
                    first = pool.submit(
                        ResultArtifact.create,
                        parent / "first",
                        identity,
                        initial_context=frozen_context(),
                        filesystem=filesystem,
                    )
                    self.assertTrue(published.wait(timeout=5))
                    second = pool.submit(other_artifact)
                    try:
                        result = second.result(timeout=1)
                        self.assertEqual(result["file_count"], 1)
                    finally:
                        release.set()
                    first.result(timeout=5)
                    second.result(timeout=5)

    def test_deterministic_same_target_create_has_one_winner(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        ready = Barrier(2)

        class RacingCreate(DeterministicFilesystemAdapter):
            def _phase(self, phase):
                super()._phase(phase)
                if phase == "after_create_prepared":
                    ready.wait(timeout=2)

        filesystem = RacingCreate(Path("/deterministic/model"))
        root, identity = Path("/caller/artifact"), artifact_identity()

        def create():
            try:
                ResultArtifact.create(
                    root,
                    identity,
                    initial_context=frozen_context(),
                    filesystem=filesystem,
                )
            except ValueError:
                return "refused"
            return "created"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = tuple(pool.map(lambda _: create(), range(2)))
        self.assertCountEqual(results, ("created", "refused"))
        self.assertEqual(
            ResultArtifact.resume(root, identity, filesystem=filesystem).checkpoint()[
                "completed_files"
            ],
            [],
        )


class ResultArtifactCreatePreflightTests(_IsolatedArtifactTest):
    def test_target_appearing_before_first_apply_refuses_without_private_staging(
        self,
    ) -> None:
        from a_share_research.result_artifact import ProductionFilesystemAdapter

        for kind in ("directory", "file", "symlink", "owned-artifact"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                parent, identity = Path(temporary).resolve(), artifact_identity()
                root = parent / "artifact"
                foreign = parent / "foreign"
                foreign.mkdir()
                (foreign / "caller-file").write_bytes(b"caller-owned")
                before = []

                class TargetAppeared(ProductionFilesystemAdapter):
                    def _phase(self, phase):
                        if phase == "before_apply":
                            if kind == "directory":
                                root.mkdir()
                                (root / "caller-file").write_bytes(b"caller-owned")
                            elif kind == "file":
                                root.write_bytes(b"caller-owned")
                            elif kind == "symlink":
                                root.symlink_to(foreign, target_is_directory=True)
                            else:
                                ResultArtifact.create(
                                    root, identity, initial_context=frozen_context()
                                )
                            before.append(filesystem_tree(parent))

                with self.assertRaises(ValueError):
                    ResultArtifact.create(
                        root,
                        identity,
                        initial_context=frozen_context(),
                        filesystem=TargetAppeared(),
                    )
                self.assertEqual(filesystem_tree(parent), before[0])
                self.assertEqual(
                    tuple(parent.glob(".result-artifact-create-*.stage")), ()
                )

    def test_ancestor_drift_before_first_apply_refuses_without_staging_in_old_parent(
        self,
    ) -> None:
        from a_share_research.result_artifact import ProductionFilesystemAdapter

        with tempfile.TemporaryDirectory() as temporary:
            parent, identity = Path(temporary).resolve(), artifact_identity()
            anchor = parent / "anchor"
            anchor.mkdir()
            root = anchor / "artifact"
            before = []

            class AncestorChanged(ProductionFilesystemAdapter):
                def _phase(self, phase):
                    if phase == "before_apply":
                        anchor.rename(parent / "original-anchor")
                        anchor.mkdir()
                        (anchor / "caller-file").write_bytes(b"caller-owned")
                        before.append(filesystem_tree(parent))

            with self.assertRaises(ValueError):
                ResultArtifact.create(
                    root,
                    identity,
                    initial_context=frozen_context(),
                    filesystem=AncestorChanged(),
                )
            self.assertEqual(filesystem_tree(parent), before[0])
            self.assertEqual(tuple((parent / "original-anchor").iterdir()), ())

    def test_deterministic_target_drift_preserves_the_callers_published_artifact(
        self,
    ) -> None:
        filesystem = DeterministicFilesystemAdapter(Path("/deterministic/model"))
        root, identity = Path("/caller/artifact"), artifact_identity()
        before = []

        def publish_target():
            caller = ResultArtifact.create(
                root, identity, initial_context=frozen_context(), filesystem=filesystem
            )
            caller.write_completed_partition(partition())
            before.append(filesystem.inventory(root))

        filesystem.pause_next_before_apply(publish_target)
        with self.assertRaises(ValueError):
            ResultArtifact.create(
                root, identity, initial_context=frozen_context(), filesystem=filesystem
            )
        self.assertEqual(filesystem.inventory(root), before[0])
        self.assertEqual(
            ResultArtifact.resume(root, identity, filesystem=filesystem).checkpoint()[
                "completed_files"
            ][0]["relative_path"],
            partition().relative_path,
        )


class ResultArtifactTrustedAnchorTests(_IsolatedArtifactTest):
    def test_explicit_trusted_domain_preserves_public_bytes_and_is_required_on_resume(
        self,
    ) -> None:
        import shutil
        from a_share_research.result_artifact import ProductionFilesystemAdapter

        with tempfile.TemporaryDirectory() as temporary:
            parent, identity = Path(temporary).resolve(), artifact_identity()
            root, anchors = parent / "artifact", parent / "trusted" / "v1"
            filesystem = ProductionFilesystemAdapter(anchor_root=anchors)
            handle = ResultArtifact.create(
                root, identity, initial_context=frozen_context(), filesystem=filesystem
            )
            handle.write_completed_partition(partition())
            receipt = handle.finalize(coverage=artifact_coverage())
            self.assertTrue(anchors.is_dir())
            self.assertEqual(
                ResultArtifact.resume(
                    root,
                    identity,
                    filesystem=ProductionFilesystemAdapter(anchor_root=anchors),
                ),
                receipt,
            )
            before = filesystem_tree(root)
            shutil.move(anchors, parent / "saved-anchors")
            with self.assertRaises(ValueError):
                ResultArtifact.resume(
                    root,
                    identity,
                    filesystem=ProductionFilesystemAdapter(anchor_root=anchors),
                )
            self.assertEqual(filesystem_tree(root), before)
            self.assertFalse(anchors.exists())

    def test_changed_artifact_journal_cannot_change_trusted_expected_ownership(
        self,
    ) -> None:
        from a_share_research.result_artifact import ProductionFilesystemAdapter

        with tempfile.TemporaryDirectory() as temporary:
            parent, identity = Path(temporary).resolve(), artifact_identity()
            root, anchors = parent / "artifact", parent / "trusted" / "v1"
            filesystem = ProductionFilesystemAdapter(anchor_root=anchors)
            handle = ResultArtifact.create(
                root, identity, initial_context=frozen_context(), filesystem=filesystem
            )
            handle.write_completed_partition(partition())
            receipt = handle.finalize(coverage=artifact_coverage())
            journal = next((root / ".protocol").glob("txn-000002-*.json"))
            payload = json.loads(journal.read_bytes())
            payload["stage_identity"][1] = "f" * 16
            journal.write_bytes(
                (
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode()
            )
            before = filesystem_tree(parent)
            with self.assertRaises(ValueError):
                ResultArtifact.validate_receipt(
                    root, identity, receipt, filesystem=filesystem
                )
            with self.assertRaises(ValueError):
                ResultArtifact.resume(root, identity, filesystem=filesystem)
            self.assertEqual(filesystem_tree(parent), before)

    def test_trusted_tail_committed_before_artifact_journal_recovers_normally(
        self,
    ) -> None:
        from a_share_research.result_artifact import ProductionFilesystemAdapter

        for operation in ("write", "finalize"):
            with (
                self.subTest(operation=operation),
                tempfile.TemporaryDirectory() as temporary,
            ):
                parent, identity = Path(temporary).resolve(), artifact_identity()
                root, anchors = parent / "artifact", parent / "trusted" / "v1"

                class InterruptedCommitment(ProductionFilesystemAdapter):
                    armed = False

                    def _phase(self, phase):
                        if (
                            self.armed
                            and phase == "after_anchor_transaction_commitment"
                        ):
                            raise KeyboardInterrupt("trusted tail committed")

                filesystem = InterruptedCommitment(anchor_root=anchors)
                handle = ResultArtifact.create(
                    root,
                    identity,
                    initial_context=frozen_context(),
                    filesystem=filesystem,
                )
                if operation == "finalize":
                    handle.write_completed_partition(partition())
                filesystem.armed = True
                with self.assertRaises(KeyboardInterrupt):
                    if operation == "write":
                        handle.write_completed_partition(partition())
                    else:
                        handle.finalize(coverage=artifact_coverage())
                recovered = ResultArtifact.resume(
                    root,
                    identity,
                    filesystem=ProductionFilesystemAdapter(anchor_root=anchors),
                )
                if operation == "write":
                    self.assertEqual(len(recovered.checkpoint()["completed_files"]), 1)
                    receipt = recovered.finalize(coverage=artifact_coverage())
                else:
                    receipt = recovered
                self.assertEqual(receipt["file_count"], 1)

    def test_published_store_missing_marker_is_not_rebootstrapped(self) -> None:
        from a_share_research.result_artifact import ProductionFilesystemAdapter

        with tempfile.TemporaryDirectory() as temporary:
            parent, identity = Path(temporary).resolve(), artifact_identity()
            anchors = parent / "trusted" / "v1"
            filesystem = ProductionFilesystemAdapter(anchor_root=anchors)
            ResultArtifact.create(
                parent / "first",
                identity,
                initial_context=frozen_context(),
                filesystem=filesystem,
            )
            (anchors / "domain.json").unlink()
            before = filesystem_tree(parent)
            with self.assertRaises(ValueError):
                ResultArtifact.create(
                    parent / "second",
                    identity,
                    initial_context=frozen_context(),
                    filesystem=filesystem,
                )
            self.assertEqual(filesystem_tree(parent), before)

    def test_cleanup_claim_move_closure_and_gc_interruptions_recover(self) -> None:
        from a_share_research.result_artifact import ProductionFilesystemAdapter

        phases = (
            "after_anchor_stage_claimed",
            "after_anchor_stage_quarantined",
            "after_anchor_cleanup_closure",
            "after_stage_cleanup_item",
            "after_stage_cleanup",
        )
        for phase in phases:
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temporary:
                parent = Path(temporary).resolve()
                root, anchors = parent / "artifact", parent / "trusted" / "v1"

                class Interrupted(ProductionFilesystemAdapter):
                    armed = False

                    def _phase(self, current: str) -> None:
                        if self.armed and current == phase:
                            self.armed = False
                            raise KeyboardInterrupt("ordinary cleanup interruption")

                adapter = Interrupted(anchor_root=anchors)
                handle = ResultArtifact.create(
                    root, artifact_identity(), filesystem=adapter
                )
                adapter.armed = True
                with self.assertRaises(KeyboardInterrupt):
                    handle.write_completed_partition(partition())
                recovered = ResultArtifact.resume(
                    root,
                    artifact_identity(),
                    filesystem=ProductionFilesystemAdapter(anchor_root=anchors),
                )
                self.assertIsInstance(recovered, ResultArtifact)
                self.assertEqual(len(recovered.checkpoint()["completed_files"]), 1)
                receipt = recovered.finalize(coverage=artifact_coverage())
                self.assertEqual(receipt["file_count"], 1)
                self.assertEqual(tuple((anchors / "quarantine").iterdir()), ())
                self.assertEqual(
                    ResultArtifact.resume(
                        root,
                        artifact_identity(),
                        filesystem=ProductionFilesystemAdapter(anchor_root=anchors),
                    ),
                    receipt,
                )

    def test_closed_external_stage_name_is_never_readopted_or_deleted(self) -> None:
        from a_share_research.result_artifact import ProductionFilesystemAdapter

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            root, anchors = parent / "artifact", parent / "trusted" / "v1"
            adapter = ProductionFilesystemAdapter(anchor_root=anchors)
            handle = ResultArtifact.create(
                root, artifact_identity(), filesystem=adapter
            )
            handle.write_completed_partition(partition())
            receipt = handle.finalize(coverage=artifact_coverage())
            journal = json.loads(
                sorted((root / ".protocol").glob("txn-*.json"))[-1].read_bytes()
            )
            foreign = parent / f".result-artifact-{journal['token']}.stage"
            foreign.mkdir()
            (foreign / "caller-file").write_text("belongs to caller")
            before = filesystem_tree(parent)
            with self.assertRaises(ValueError):
                ResultArtifact.resume(root, artifact_identity(), filesystem=adapter)
            self.assertEqual(filesystem_tree(parent), before)
            self.assertEqual(receipt["file_count"], 1)

    def test_real_process_anchor_bootstrap_transaction_and_cleanup_matrix(self) -> None:
        import os
        import subprocess
        import sys
        from a_share_research.result_artifact import ProductionFilesystemAdapter

        matrix = (
            *(
                ("create", phase, False)
                for phase in (
                    "after_anchor_directory_created",
                    "after_anchor_bootstrap_directory_created",
                    "after_anchor_record_created",
                    "after_anchor_record_written",
                    "after_anchor_record_publication",
                    "after_anchor_bootstrap_prepared",
                    "after_anchor_store_publication",
                    "after_anchor_record_created@2",
                    "after_anchor_record_written@2",
                    "after_anchor_record_publication@2",
                    "after_anchor_creation_commitment",
                )
            ),
            *(
                (action, phase, committed)
                for action in ("write", "finalize")
                for phase, committed in (
                    ("after_anchor_record_created", False),
                    ("after_anchor_record_written", False),
                    ("after_anchor_record_publication", True),
                    ("after_anchor_transaction_commitment", True),
                    ("after_anchor_journal_published", True),
                    ("after_anchor_stage_claimed", True),
                    ("after_anchor_stage_quarantined", True),
                    ("after_anchor_cleanup_closure", True),
                    ("after_stage_cleanup_item", True),
                    ("after_stage_cleanup", True),
                )
            ),
        )
        for action, phase, committed in matrix:
            with (
                self.subTest(action=action, phase=phase),
                tempfile.TemporaryDirectory() as temporary,
            ):
                parent = Path(temporary).resolve()
                root, anchors = (
                    parent / "artifact",
                    parent / "trusted" / "anchors" / "v1",
                )
                adapter = ProductionFilesystemAdapter(anchor_root=anchors)
                if action != "create":
                    ResultArtifact.create(root, artifact_identity(), filesystem=adapter)
                process = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        _PROCESS,
                        str(root),
                        action,
                        phase,
                        str(anchors),
                    ],
                    cwd=Path(__file__).resolve().parents[1],
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
                self.assertEqual(process.returncode, 77, process.stderr)
                if action == "create":
                    self.assertFalse(root.exists())
                recovery = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        _PROCESS,
                        str(root),
                        "create" if action == "create" else "resume",
                        "",
                        str(anchors),
                    ],
                    cwd=Path(__file__).resolve().parents[1],
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
                self.assertEqual(recovery.returncode, 0, recovery.stderr)
                self.assertEqual(
                    recovery.stdout.strip() == "partial",
                    not (action == "finalize" and committed),
                )
                # The parent only reads the state already recovered by a new
                # process; it is not the recovery worker or an authority cache.
                recovered = ResultArtifact.resume(
                    root,
                    artifact_identity(),
                    filesystem=ProductionFilesystemAdapter(anchor_root=anchors),
                )
                if action == "finalize" and committed:
                    self.assertIsInstance(recovered, dict)
                    before = filesystem_tree(parent)
                    self.assertEqual(
                        ResultArtifact.resume(
                            root, artifact_identity(), filesystem=adapter
                        ),
                        recovered,
                    )
                    self.assertEqual(filesystem_tree(parent), before)
                else:
                    self.assertIsInstance(recovered, ResultArtifact)
                    count = 1 if action == "write" and committed else 0
                    self.assertEqual(
                        len(recovered.checkpoint()["completed_files"]), count
                    )
                    if action == "write" and not committed:
                        recovered.write_completed_partition(partition())
                        count = 1
                    self.assertEqual(
                        recovered.finalize(coverage=artifact_coverage())["file_count"],
                        count,
                    )

    def test_anchor_wrong_domain_record_damage_and_permissions_are_zero_mutation(
        self,
    ) -> None:
        import os
        from a_share_research.result_artifact import ProductionFilesystemAdapter

        for change in (
            "wrong-domain",
            "missing-record",
            "damaged-record",
            "record-mode",
            "directory-mode",
        ):
            with (
                self.subTest(change=change),
                tempfile.TemporaryDirectory() as temporary,
            ):
                parent = Path(temporary).resolve()
                root, anchors = (
                    parent / "artifact",
                    parent / "trusted" / "anchors" / "v1",
                )
                adapter = ProductionFilesystemAdapter(anchor_root=anchors)
                handle = ResultArtifact.create(
                    root, artifact_identity(), filesystem=adapter
                )
                receipt = handle.finalize(coverage=artifact_coverage())
                record = next(
                    path
                    for path in anchors.glob("*.json")
                    if path.name != "domain.json"
                )
                if change == "wrong-domain":
                    wrong = parent / "other" / "anchors" / "v1"
                    ResultArtifact.create(
                        parent / "other-artifact",
                        artifact_identity(),
                        filesystem=ProductionFilesystemAdapter(anchor_root=wrong),
                    )
                    adapter = ProductionFilesystemAdapter(anchor_root=wrong)
                elif change == "missing-record":
                    record.unlink()
                elif change == "damaged-record":
                    record.write_bytes(b"ordinary damaged local record")
                elif change == "record-mode":
                    os.chmod(record, 0o644)
                else:
                    os.chmod(anchors, 0o755)
                before = filesystem_tree(parent)
                with self.assertRaises(ValueError):
                    ResultArtifact.resume(root, artifact_identity(), filesystem=adapter)
                with self.assertRaises(ValueError):
                    ResultArtifact.validate_receipt(
                        root, artifact_identity(), receipt, filesystem=adapter
                    )
                self.assertEqual(filesystem_tree(parent), before)

    def test_anchor_symlink_unverifiable_acl_same_filesystem_and_overlap_preflight(
        self,
    ) -> None:
        import os
        from unittest.mock import patch
        import a_share_research.result_artifact as module
        from a_share_research.result_artifact import ProductionFilesystemAdapter

        for change in ("symlink", "acl", "different-filesystem", "overlap"):
            with (
                self.subTest(change=change),
                tempfile.TemporaryDirectory() as temporary,
            ):
                parent = Path(temporary).resolve()
                anchors, root = (
                    parent / "trusted" / "anchors" / "v1",
                    parent / "new-artifact",
                )
                ResultArtifact.create(
                    parent / "existing-artifact",
                    artifact_identity(),
                    filesystem=ProductionFilesystemAdapter(anchor_root=anchors),
                )
                if change == "symlink":
                    alias = parent / "alias"
                    alias.symlink_to(anchors.parent, target_is_directory=True)
                    adapter = ProductionFilesystemAdapter(anchor_root=alias / "v1")
                elif change == "overlap":
                    adapter = ProductionFilesystemAdapter(
                        anchor_root=root / ".state" / "anchors" / "v1"
                    )
                else:
                    adapter = ProductionFilesystemAdapter(anchor_root=anchors)
                before = filesystem_tree(parent)
                if change == "acl":
                    with patch.object(
                        adapter,
                        "_trusted_acl",
                        side_effect=ValueError("unverifiable ACL"),
                    ):
                        with self.assertRaises(ValueError):
                            ResultArtifact.create(
                                root, artifact_identity(), filesystem=adapter
                            )
                elif change == "different-filesystem":
                    native = os.fstat
                    anchor_inode = anchors.stat().st_ino

                    def different(descriptor):
                        value = native(descriptor)
                        if value.st_ino == anchor_inode:
                            values = list(value)
                            values[2] += 1
                            return os.stat_result(values)
                        return value

                    with patch.object(module.os, "fstat", side_effect=different):
                        with self.assertRaisesRegex(ValueError, "same filesystem"):
                            ResultArtifact.create(
                                root, artifact_identity(), filesystem=adapter
                            )
                else:
                    with self.assertRaises(ValueError):
                        ResultArtifact.create(
                            root, artifact_identity(), filesystem=adapter
                        )
                self.assertEqual(filesystem_tree(parent), before)
                self.assertFalse(root.exists())

    def test_deterministic_anchor_fault_phases_match_transaction_recovery(self) -> None:
        for action in ("write", "finalize"):
            for phase, committed in (
                ("after_anchor_record_created", False),
                ("after_anchor_record_written", False),
                ("after_anchor_record_publication", True),
                ("after_anchor_transaction_commitment", True),
                ("after_anchor_stage_claimed", True),
                ("after_anchor_stage_quarantined", True),
                ("after_anchor_cleanup_closure", True),
            ):
                with self.subTest(action=action, phase=phase):
                    filesystem = DeterministicFilesystemAdapter(
                        Path("/deterministic/model")
                    )
                    root = Path("/caller/artifact")
                    handle = ResultArtifact.create(
                        root, artifact_identity(), filesystem=filesystem
                    )
                    filesystem.interrupt_next(phase)
                    with self.assertRaises(KeyboardInterrupt):
                        if action == "write":
                            handle.write_completed_partition(partition())
                        else:
                            handle.finalize(coverage=artifact_coverage())
                    recovered = ResultArtifact.resume(
                        root, artifact_identity(), filesystem=filesystem
                    )
                    if action == "finalize" and committed:
                        self.assertEqual(recovered["file_count"], 0)
                    else:
                        self.assertEqual(
                            len(recovered.checkpoint()["completed_files"]),
                            int(action == "write" and committed),
                        )
                    filesystem.remove_trusted_record(root)
                    before = filesystem.inventory(root)
                    with self.assertRaises(ValueError):
                        ResultArtifact.resume(
                            root, artifact_identity(), filesystem=filesystem
                        )
                    self.assertEqual(filesystem.inventory(root), before)

    def test_anchor_capacity_is_preflighted_with_full_future_closure(self) -> None:
        from unittest.mock import patch
        import a_share_research.result_artifact as module

        filesystem = DeterministicFilesystemAdapter(Path("/deterministic/model"))
        root = Path("/caller/artifact")
        handle = ResultArtifact.create(root, artifact_identity(), filesystem=filesystem)
        before = filesystem.inventory(root)
        with patch.object(module, "_MAX_ANCHOR_BYTES", 1024):
            with self.assertRaisesRegex(ValueError, "trusted record exceeds capacity"):
                handle.write_completed_partition(partition())
        self.assertEqual(filesystem.inventory(root), before)
        handle.write_completed_partition(partition())
        self.assertEqual(handle.finalize(coverage=artifact_coverage())["file_count"], 1)

    def test_real_anchor_recovery_can_be_interrupted_again(self) -> None:
        import os
        import subprocess
        import sys
        from a_share_research.result_artifact import ProductionFilesystemAdapter

        for action in ("write", "finalize"):
            with (
                self.subTest(action=action),
                tempfile.TemporaryDirectory() as temporary,
            ):
                parent = Path(temporary).resolve()
                root, anchors = (
                    parent / "artifact",
                    parent / "trusted" / "anchors" / "v1",
                )
                adapter = ProductionFilesystemAdapter(anchor_root=anchors)
                ResultArtifact.create(
                    root,
                    artifact_identity(),
                    initial_context=frozen_context(),
                    filesystem=adapter,
                )
                for current_action, phase in (
                    (action, "after_anchor_transaction_commitment"),
                    ("resume", "after_anchor_stage_quarantined"),
                    ("resume", "after_anchor_cleanup_closure"),
                ):
                    result = subprocess.run(
                        [
                            sys.executable,
                            "-c",
                            _PROCESS,
                            str(root),
                            current_action,
                            phase,
                            str(anchors),
                        ],
                        cwd=Path(__file__).resolve().parents[1],
                        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                        capture_output=True,
                        text=True,
                        timeout=15,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 77, result.stderr)
                recovered = ResultArtifact.resume(
                    root, artifact_identity(), filesystem=adapter
                )
                if action == "write":
                    self.assertEqual(
                        recovered.checkpoint()["frozen_context"],
                        json.loads(frozen_context().content_bytes),
                    )
                    self.assertEqual(len(recovered.checkpoint()["completed_files"]), 1)
                    recovered = recovered.finalize(coverage=artifact_coverage())
                before = filesystem_tree(parent)
                self.assertEqual(
                    ResultArtifact.resume(
                        root, artifact_identity(), filesystem=adapter
                    ),
                    recovered,
                )
                self.assertEqual(filesystem_tree(parent), before)

    def test_unpublished_bootstrap_marker_damage_refuses_before_any_mutation(
        self,
    ) -> None:
        from a_share_research.result_artifact import ProductionFilesystemAdapter

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            root, anchors = parent / "artifact", parent / "trusted" / "anchors" / "v1"

            class Interrupted(ProductionFilesystemAdapter):
                def _phase(self, phase: str) -> None:
                    if phase == "after_anchor_bootstrap_prepared":
                        raise KeyboardInterrupt("bootstrap prepared")

            with self.assertRaises(KeyboardInterrupt):
                ResultArtifact.create(
                    root,
                    artifact_identity(),
                    filesystem=Interrupted(anchor_root=anchors),
                )
            marker = anchors.with_name("v1.preparing") / "domain.json"
            marker.write_bytes(b"damaged committed bootstrap marker")
            before = filesystem_tree(parent)
            with self.assertRaises(ValueError):
                ResultArtifact.create(
                    root,
                    artifact_identity(),
                    filesystem=ProductionFilesystemAdapter(anchor_root=anchors),
                )
            self.assertEqual(filesystem_tree(parent), before)
            self.assertFalse(root.exists())

    def test_related_trusted_preparation_and_quarantine_inventory_are_validated(
        self,
    ) -> None:
        import os
        from a_share_research.result_artifact import ProductionFilesystemAdapter

        for change in ("pending-mode", "unknown-quarantine"):
            with (
                self.subTest(change=change),
                tempfile.TemporaryDirectory() as temporary,
            ):
                parent = Path(temporary).resolve()
                root, anchors = (
                    parent / "artifact",
                    parent / "trusted" / "anchors" / "v1",
                )
                adapter = ProductionFilesystemAdapter(anchor_root=anchors)
                handle = ResultArtifact.create(
                    root, artifact_identity(), filesystem=adapter
                )
                handle.finalize(coverage=artifact_coverage())
                record = next(
                    path
                    for path in anchors.glob("*.json")
                    if path.name != "domain.json"
                )
                if change == "pending-mode":
                    pending = record.with_name(record.name + ".pending")
                    pending.write_bytes(b"interrupted private preparation")
                    os.chmod(pending, 0o644)
                else:
                    (anchors / "quarantine" / (record.name + "." + "f" * 64)).mkdir()
                before = filesystem_tree(parent)
                with self.assertRaises(ValueError):
                    ResultArtifact.resume(root, artifact_identity(), filesystem=adapter)
                self.assertEqual(filesystem_tree(parent), before)

    def test_lost_or_replaced_quarantine_refuses_new_create_before_any_mutation(
        self,
    ) -> None:
        from a_share_research.result_artifact import ProductionFilesystemAdapter

        for change in ("missing", "replacement", "before-apply"):
            with (
                self.subTest(change=change),
                tempfile.TemporaryDirectory() as temporary,
            ):
                parent = Path(temporary).resolve()
                anchors = parent / "trusted" / "anchors" / "v1"
                adapter = ProductionFilesystemAdapter(anchor_root=anchors)
                ResultArtifact.create(
                    parent / "original", artifact_identity(), filesystem=adapter
                )
                quarantine = anchors / "quarantine"
                if change == "replacement":
                    quarantine.rename(anchors / "original-quarantine")
                    quarantine.mkdir(mode=0o700)
                elif change == "missing":
                    quarantine.rmdir()
                before = filesystem_tree(parent)
                expected = []

                class Drifted(ProductionFilesystemAdapter):
                    def _phase(self, phase: str) -> None:
                        if change == "before-apply" and phase == "before_apply":
                            quarantine.rmdir()
                            expected.append(filesystem_tree(parent))

                with self.assertRaises(ValueError):
                    ResultArtifact.create(
                        parent / "new-artifact",
                        artifact_identity(),
                        filesystem=Drifted(anchor_root=anchors),
                    )
                self.assertEqual(
                    filesystem_tree(parent), expected[0] if expected else before
                )
                self.assertFalse((parent / "new-artifact").exists())

    def test_last_quarantine_gc_fsync_failure_requires_fresh_recovery_fence(
        self,
    ) -> None:
        import os
        import subprocess
        import sys
        from a_share_research.result_artifact import ProductionFilesystemAdapter

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            root, anchors = parent / "artifact", parent / "trusted" / "anchors" / "v1"

            class Failed(ProductionFilesystemAdapter):
                removed = False

                def _phase(self, phase: str) -> None:
                    if phase == "after_stage_cleanup":
                        self.removed = True

                def _sync_fd(self, descriptor: int, *, directory: bool = False) -> None:
                    if (
                        self.removed
                        and directory
                        and os.fstat(descriptor).st_ino
                        == (anchors / "quarantine").stat().st_ino
                    ):
                        raise OSError("quarantine directory durability failure")
                    super()._sync_fd(descriptor, directory=directory)

            handle = ResultArtifact.create(
                root, artifact_identity(), filesystem=Failed(anchor_root=anchors)
            )
            with self.assertRaises(ValueError):
                handle.finalize(coverage=artifact_coverage())
            before = filesystem_tree(parent)
            worker = r"""
import os,sys,json
from pathlib import Path
from tests.test_result_artifact import artifact_identity
from a_share_research.result_artifact import ResultArtifact,ProductionFilesystemAdapter
root,anchors=Path(sys.argv[1]),Path(sys.argv[2])
class Failed(ProductionFilesystemAdapter):
    attempts=0
    def _sync_fd(self,descriptor,*,directory=False):
        if directory and os.fstat(descriptor).st_ino==(anchors/'quarantine').stat().st_ino:
            self.attempts+=1
            raise OSError('quarantine fence still unavailable')
        super()._sync_fd(descriptor,directory=directory)
adapter=Failed(anchor_root=anchors)
try:
    ResultArtifact.recover_finalized_receipt(root,artifact_identity(),filesystem=adapter)
except ValueError:
    print(json.dumps({'rejected':True,'attempts':adapter.attempts}))
else:
    print(json.dumps({'rejected':False,'attempts':adapter.attempts}))
"""
            result = subprocess.run(
                [sys.executable, "-c", worker, str(root), str(anchors)],
                cwd=Path(__file__).resolve().parents[1],
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            outcome = json.loads(result.stdout)
            self.assertTrue(outcome["rejected"], outcome)
            self.assertGreater(outcome["attempts"], 0)
            self.assertEqual(filesystem_tree(parent), before)
            receipt = ResultArtifact.recover_finalized_receipt(
                root,
                artifact_identity(),
                filesystem=ProductionFilesystemAdapter(anchor_root=anchors),
            )
            self.assertEqual(receipt["file_count"], 0)

    def test_deterministic_quarantine_loss_is_preflighted_in_both_windows(self) -> None:
        for before_apply in (False, True):
            with self.subTest(before_apply=before_apply):
                filesystem = DeterministicFilesystemAdapter(
                    Path("/deterministic/model")
                )
                root = Path("/caller/original")
                ResultArtifact.create(root, artifact_identity(), filesystem=filesystem)
                before = filesystem.inventory(root)
                if before_apply:
                    filesystem.pause_next_before_apply(
                        filesystem.remove_trusted_quarantine
                    )
                else:
                    filesystem.remove_trusted_quarantine()
                with self.assertRaises(ValueError):
                    ResultArtifact.create(
                        Path("/caller/new"), artifact_identity(), filesystem=filesystem
                    )
                self.assertFalse(filesystem.exists(Path("/caller/new")))
                self.assertEqual(filesystem.inventory(root), before)
                with self.assertRaises(ValueError):
                    ResultArtifact.resume(
                        root, artifact_identity(), filesystem=filesystem
                    )

    def test_rejected_create_does_not_bootstrap_trusted_state(self) -> None:
        from a_share_research.result_artifact import ProductionFilesystemAdapter

        with tempfile.TemporaryDirectory() as temporary:
            parent, identity = Path(temporary).resolve(), artifact_identity()
            root, anchors = parent / "artifact", parent / "trusted" / "v1"
            root.mkdir()
            (root / "caller-file").write_bytes(b"caller-owned")
            before = filesystem_tree(parent)
            with self.assertRaises(ValueError):
                ResultArtifact.create(
                    root,
                    identity,
                    initial_context=frozen_context(),
                    filesystem=ProductionFilesystemAdapter(anchor_root=anchors),
                )
            self.assertEqual(filesystem_tree(parent), before)
            self.assertFalse(anchors.exists())
