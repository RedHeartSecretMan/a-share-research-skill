from __future__ import annotations

import os
import tempfile
import unittest
from collections.abc import Callable, Iterator, Mapping, Sequence
from decimal import Decimal
from itertools import product

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

import a_share_research.result_artifact_inputs as result_artifact_inputs_module  # noqa: E402
from a_share_research.result_artifact_inputs import (  # noqa: E402
    PreparedResultArtifactFile,
    prepare_candidate,
    prepare_completed_partition,
)

PreparedFactory = Callable[[Mapping[str, object]], PreparedResultArtifactFile]


class _StatefulString(str):
    def __str__(self) -> str:
        return "../changed-after-validation"

    def __hash__(self) -> int:
        return hash("universe")

    def __eq__(self, other: object) -> bool:
        return other in {"universe", "SSE", "1.0", "SSE:600519"}


class _StatefulInt(int):
    def __new__(cls, value: int) -> _StatefulInt:
        return super().__new__(cls, value)

    def __index__(self) -> int:
        return 99_999


class _OnePassMapping(Mapping[str, object]):
    def __init__(self, values: dict[str, object]) -> None:
        self.values = values
        self.traversals = 0

    def __getitem__(self, key: str) -> object:
        raise AssertionError("snapshot must consume mapping items directly")

    def __iter__(self) -> Iterator[str]:
        raise AssertionError("snapshot must not traverse a mapping twice")

    def __len__(self) -> int:
        raise AssertionError("snapshot must not pre-read mapping length")

    def items(self):  # type: ignore[no-untyped-def]
        self.traversals += 1
        if self.traversals > 1:
            raise AssertionError("mapping was traversed more than once")
        return iter(tuple(self.values.items()))


class _OnePassSequence(Sequence[object]):
    def __init__(self, values: list[object]) -> None:
        self.values = values
        self.traversals = 0

    def __getitem__(self, index: int) -> object:
        raise AssertionError("snapshot must consume the sequence iterator")

    def __len__(self) -> int:
        raise AssertionError("snapshot must not pre-read sequence length")

    def __iter__(self) -> Iterator[object]:
        self.traversals += 1
        if self.traversals > 1:
            raise AssertionError("sequence was traversed more than once")
        return iter(tuple(self.values))


class _DuplicateKeyMapping(_OnePassMapping):
    def items(self):  # type: ignore[no-untyped-def]
        self.traversals += 1
        return iter((("status", "safe"), ("status", "changed")))


class _LeakingMapping(_OnePassMapping):
    def items(self):  # type: ignore[no-untyped-def]
        raise ValueError("rejected /private/tmp/do-not-leak.json")


class _RuntimeLeakingMapping(_OnePassMapping):
    def items(self):  # type: ignore[no-untyped-def]
        raise RuntimeError("failed at /private/synthetic/do-not-leak.json")


class _ExhaustedMapping(_OnePassMapping):
    def items(self):  # type: ignore[no-untyped-def]
        raise MemoryError("runtime exhaustion at /private/synthetic/secret.bin")


class _RuntimeLeakingClassSequence(Sequence[object]):
    @property
    def __class__(self) -> type[object]:
        raise RuntimeError("failed at /private/synthetic/do-not-leak.json")

    def __getitem__(self, index: int) -> object:
        raise AssertionError("the rejected sequence must not be traversed")

    def __len__(self) -> int:
        raise AssertionError("the rejected sequence must not be measured")


class _SwitchingContainer:
    def __init__(self, first_kind: type[object]) -> None:
        self.first_kind = first_kind
        self.kind_observations = 0

    @property
    def __class__(self) -> type[object]:
        self.kind_observations += 1
        if self.kind_observations == 1:
            return self.first_kind
        return Sequence if self.first_kind is Mapping else Mapping

    def items(self):  # type: ignore[no-untyped-def]
        return iter((("status", "limited"),))

    def __iter__(self) -> Iterator[object]:
        return iter(("limited",))


class _OneObservationMapping(_OnePassMapping):
    def __init__(self, values: dict[str, object]) -> None:
        super().__init__(values)
        self.kind_observations = 0

    @property
    def __class__(self) -> type[object]:
        self.kind_observations += 1
        if self.kind_observations > 1:
            raise RuntimeError("caller container kind was observed more than once")
        return Mapping


class _ManyRecords(Sequence[object]):
    def __getitem__(self, index: int) -> object:
        raise AssertionError("record limit must use one-pass iteration")

    def __len__(self) -> int:
        raise AssertionError("record limit must not pre-read sequence length")

    def __iter__(self) -> Iterator[object]:
        return ({} for _ in range(100_001))


class ResultArtifactInputTests(unittest.TestCase):
    def test_completed_partition_prepares_canonical_immutable_file(self) -> None:
        prepared = prepare_completed_partition(
            kind="universe",
            exchange="SSE",
            shard_index=0,
            schema_version="1.0",
            records=(
                {
                    "score": Decimal("1.20"),
                    "eligible": True,
                    "canonical_security_id": "SSE:600519",
                },
                {
                    "score": None,
                    "canonical_security_id": "SSE:601138",
                    "eligible": False,
                },
            ),
        )

        self.assertIsInstance(prepared, PreparedResultArtifactFile)
        self.assertEqual(
            prepared.relative_path,
            "partitions/universe/SSE/part-00000.jsonl",
        )
        self.assertEqual(prepared.schema_version, "1.0")
        self.assertEqual(
            prepared.content_bytes,
            b'{"canonical_security_id":"SSE:600519","eligible":true,'
            b'"score":"1.20"}\n'
            b'{"canonical_security_id":"SSE:601138","eligible":false,'
            b'"score":null}\n',
        )
        self.assertEqual(
            prepared.sha256,
            "9c5ef5d8f6e63ff316d22199b7354d3aefd07e0a70fe8a533d7cb023470fb553",
        )
        self.assertEqual(prepared.byte_count, 139)
        self.assertEqual(prepared.record_count, 2)

    def test_candidate_prepares_canonical_immutable_file(self) -> None:
        prepared = prepare_candidate(
            canonical_security_id="BSE:920001",
            schema_version="1.0",
            document={
                "schema_version": "1.0",
                "brief": {"status": "limited", "score": Decimal("2.50")},
            },
        )

        self.assertEqual(prepared.relative_path, "candidates/BSE-920001.json")
        self.assertEqual(prepared.schema_version, "1.0")
        self.assertEqual(
            prepared.content_bytes,
            b'{"brief":{"score":"2.50","status":"limited"},"schema_version":"1.0"}\n',
        )
        self.assertEqual(
            prepared.sha256,
            "a05ecfd5141f912ba2617d9a05254738fc0bc17440ee4636962ac934e203e49d",
        )
        self.assertEqual(prepared.byte_count, 69)
        self.assertEqual(prepared.record_count, 1)
        with self.assertRaises(AttributeError):
            prepared.relative_path = "candidates/SSE-600519.json"
        for field_name, replacement in (
            ("relative_path", "../../outside.json"),
            ("schema_version", "forged"),
            ("content_bytes", b"changed\n"),
            ("sha256", "0" * 64),
            ("byte_count", 0),
            ("record_count", 0),
        ):
            with self.subTest(field_name=field_name):
                with self.assertRaises(AttributeError):
                    object.__setattr__(prepared, field_name, replacement)
        self.assertEqual(prepared.relative_path, "candidates/BSE-920001.json")
        self.assertEqual(prepared.schema_version, "1.0")
        self.assertEqual(
            prepared.content_bytes,
            b'{"brief":{"score":"2.50","status":"limited"},"schema_version":"1.0"}\n',
        )
        self.assertEqual(
            prepared.sha256,
            "a05ecfd5141f912ba2617d9a05254738fc0bc17440ee4636962ac934e203e49d",
        )
        self.assertEqual(prepared.byte_count, len(prepared.content_bytes))
        self.assertEqual(prepared.record_count, 1)
        with self.assertRaises(TypeError):
            PreparedResultArtifactFile(
                relative_path="../../caller-constructed.json",
                schema_version="1.0",
                content_bytes=b"{}\n",
                sha256="0" * 64,
                byte_count=3,
                record_count=1,
            )
        with self.assertRaises(TypeError):
            PreparedResultArtifactFile()
        forged_values = (
            "../../caller-constructed.json",
            "1.0",
            b"{}\n",
            "0" * 64,
            3,
            1,
        )
        with self.assertRaises(TypeError):
            tuple.__new__(PreparedResultArtifactFile, forged_values)
        with self.assertRaises(TypeError):
            object.__new__(PreparedResultArtifactFile)

    def test_prepared_file_authority_cannot_be_recreated_by_callers(self) -> None:
        prepared_files = (
            prepare_candidate(
                canonical_security_id="SSE:600519",
                schema_version="1.0",
                document={},
            ),
            prepare_completed_partition(
                kind="universe",
                exchange="SSE",
                shard_index=0,
                schema_version="1.0",
                records=({},),
            ),
        )
        forged_values = (
            "../../caller-constructed.json",
            "forged",
            b"bad\n",
            "0" * 64,
            4,
            -1,
        )
        for prepared in prepared_files:
            with self.subTest(factory_path=prepared.relative_path):
                with self.assertRaises(TypeError):
                    tuple.__new__(type(prepared), forged_values)
                direct_allocation = object.__new__(type(prepared))
                self.assertNotIsInstance(
                    direct_allocation,
                    PreparedResultArtifactFile,
                )

        with self.assertRaises(TypeError):

            class _CallerForgedPreparedFile(PreparedResultArtifactFile):
                @property
                def relative_path(self) -> str:
                    return "../../caller-constructed.json"

                @property
                def schema_version(self) -> str:
                    return "forged"

                @property
                def content_bytes(self) -> bytes:
                    return b"bad\n"

                @property
                def sha256(self) -> str:
                    return "0" * 64

                @property
                def byte_count(self) -> int:
                    return 4

                @property
                def record_count(self) -> int:
                    return -1

    def test_coordinates_require_exact_canonical_builtin_tokens(self) -> None:
        valid_partition = prepare_completed_partition(
            kind="ranking",
            exchange="SZSE",
            shard_index=99_999,
            schema_version="factor-record@1.0+draft",
            records=(),
        )
        self.assertEqual(
            valid_partition.relative_path,
            "partitions/ranking/SZSE/part-99999.jsonl",
        )

        partition_coordinates: tuple[dict[str, object], ...] = (
            {"kind": _StatefulString("universe")},
            {"kind": "../universe"},
            {"exchange": _StatefulString("SSE")},
            {"exchange": "sse"},
            {"shard_index": _StatefulInt(0)},
            {"shard_index": True},
            {"shard_index": -1},
            {"shard_index": 100_000},
            {"schema_version": _StatefulString("1.0")},
            {"schema_version": "a" * 129},
            {"schema_version": "schema/1.0"},
            {"schema_version": "schema\\1.0"},
            {"schema_version": "schema..1.0"},
            {"schema_version": "模式1.0"},
        )
        for changed in partition_coordinates:
            with self.subTest(partition=changed):
                arguments: dict[str, object] = {
                    "kind": "universe",
                    "exchange": "SSE",
                    "shard_index": 0,
                    "schema_version": "1.0",
                    "records": (),
                    **changed,
                }
                with self.assertRaises(ValueError) as raised:
                    prepare_completed_partition(**arguments)  # type: ignore[arg-type]
                self.assertNotIn("../changed-after-validation", str(raised.exception))

        candidate_coordinates = (
            _StatefulString("SSE:600519"),
            "SH:600519",
            "SSE:60051",
            "SSE:6005190",
            "SSE:６００５１９",
            "SSE/600519",
            "../SSE:600519",
        )
        for identity in candidate_coordinates:
            with self.subTest(candidate=identity):
                with self.assertRaises(ValueError) as raised:
                    prepare_candidate(
                        canonical_security_id=identity,
                        schema_version="1.0",
                        document={},
                    )
                self.assertNotIn(str(identity), str(raised.exception))

        for schema_version in (
            _StatefulString("1.0"),
            "",
            ".",
            "schema/path",
            "schema\\path",
            "schema..1.0",
        ):
            with self.subTest(candidate_schema=schema_version):
                with self.assertRaises(ValueError):
                    prepare_candidate(
                        canonical_security_id="SSE:600519",
                        schema_version=schema_version,
                        document={},
                    )

    def test_caller_containers_are_snapshotted_once_and_detached(self) -> None:
        shared = _OnePassMapping({"status": "limited"})
        nested_sequence = _OnePassSequence([shared, shared])
        root = _OnePassMapping({"values": nested_sequence})

        prepared = prepare_candidate(
            canonical_security_id="SSE:600519",
            schema_version="1.0",
            document=root,
        )

        self.assertEqual(root.traversals, 1)
        self.assertEqual(nested_sequence.traversals, 1)
        self.assertEqual(shared.traversals, 1)
        self.assertEqual(
            prepared.content_bytes,
            b'{"values":[{"status":"limited"},{"status":"limited"}]}\n',
        )
        root.values["late"] = "changed"
        nested_sequence.values.append("changed")
        shared.values["status"] = "changed"
        self.assertEqual(
            prepared.content_bytes,
            b'{"values":[{"status":"limited"},{"status":"limited"}]}\n',
        )

        partition_mapping = _OnePassMapping({"status": "limited"})
        records = _OnePassSequence([partition_mapping])
        partition = prepare_completed_partition(
            kind="factors",
            exchange="BSE",
            shard_index=7,
            schema_version="1.0",
            records=records,  # type: ignore[arg-type]
        )
        self.assertEqual(records.traversals, 1)
        self.assertEqual(partition_mapping.traversals, 1)
        self.assertEqual(partition.content_bytes, b'{"status":"limited"}\n')

        with self.assertRaises(ValueError):
            prepare_candidate(
                canonical_security_id="SSE:600519",
                schema_version="1.0",
                document=_DuplicateKeyMapping({}),
            )

    def test_container_kind_is_observed_once_and_the_first_view_is_detached(
        self,
    ) -> None:
        for first_kind, expected_nested in (
            (Mapping, b'{"nested":{"status":"limited"}}\n'),
            (Sequence, b'{"nested":["limited"]}\n'),
        ):
            for prepare in (
                lambda document: prepare_candidate(
                    canonical_security_id="SSE:600519",
                    schema_version="1.0",
                    document={"nested": document},
                ),
                lambda document: prepare_completed_partition(
                    kind="universe",
                    exchange="SSE",
                    shard_index=0,
                    schema_version="1.0",
                    records=({"nested": document},),
                ),
            ):
                with self.subTest(first_kind=first_kind, prepare=prepare):
                    switching = _SwitchingContainer(first_kind)
                    prepared = prepare(switching)
                    self.assertEqual(switching.kind_observations, 1)
                    self.assertEqual(prepared.content_bytes, expected_nested)

    def test_aliased_container_kind_is_observed_once_and_detached_once(
        self,
    ) -> None:
        for prepare in (
            lambda document: prepare_candidate(
                canonical_security_id="SSE:600519",
                schema_version="1.0",
                document={"first": document, "second": document},
            ),
            lambda document: prepare_completed_partition(
                kind="universe",
                exchange="SSE",
                shard_index=0,
                schema_version="1.0",
                records=({"first": document, "second": document},),
            ),
        ):
            with self.subTest(prepare=prepare):
                shared = _OneObservationMapping({"status": "limited"})
                prepared = prepare(shared)
                self.assertEqual(shared.kind_observations, 1)
                self.assertEqual(shared.traversals, 1)
                self.assertEqual(
                    prepared.content_bytes,
                    b'{"first":{"status":"limited"},"second":{"status":"limited"}}\n',
                )

    def test_aliases_obey_logical_item_and_placement_depth_bounds(self) -> None:
        accepted_shared = [None] * 4_998
        accepted_document = {
            "first": accepted_shared,
            "second": accepted_shared,
        }
        prepare_candidate(
            canonical_security_id="SSE:600519",
            schema_version="1.0",
            document=accepted_document,
        )
        prepare_completed_partition(
            kind="universe",
            exchange="SSE",
            shard_index=0,
            schema_version="1.0",
            records=(accepted_document,),
        )

        rejected_shared = [None] * 4_999
        self._assert_document_rejects_for_both_kinds(
            {"first": rejected_shared, "second": rejected_shared}
        )

        shared: object = None
        for _ in range(8):
            shared = {"nested": shared}

        candidate_deep: object = shared
        for _ in range(24):
            candidate_deep = {"nested": candidate_deep}
        with self.assertRaises(ValueError):
            prepare_candidate(
                canonical_security_id="SSE:600519",
                schema_version="1.0",
                document={"shallow": shared, "deep": candidate_deep},
            )

        partition_deep: object = shared
        for _ in range(23):
            partition_deep = {"nested": partition_deep}
        with self.assertRaises(ValueError):
            prepare_completed_partition(
                kind="universe",
                exchange="SSE",
                shard_index=0,
                schema_version="1.0",
                records=({"shallow": shared, "deep": partition_deep},),
            )

    def test_nested_paths_and_transport_envelopes_reject_without_leaks(self) -> None:
        rejected_documents: tuple[Mapping[str, object], ...] = (
            {"note": "/private/tmp/secret.json"},
            {"note": "failed at /private/tmp/secret.json"},
            {"note": "failed|/private/tmp/secret.json"},
            {"note": "path=/"},
            {"note": "path|/"},
            {"note": "file:///private/tmp/secret.json"},
            {"note": "prefixfile://safe then file:///private/tmp/secret.json"},
            {"note": "ß file:///private/tmp/secret.json"},
            {"note": "///private/tmp/secret.json"},
            {"file_paths": [r"C:\private\secret.json"]},
            {"file_paths": [r"prefix_C:\private\secret.json"]},
            {"filePaths": [r"failed=C:\private\secret.json;retry=false"]},
            {"filePaths": [r"failed|C:\private\secret.json"]},
            {"sourcePath": r"\\server\share\secret.json"},
            {"sourcePath": r"\\\server\share\secret.json"},
            {"sourcePath": r"failed=\\server\share\secret.json;retry=false"},
            {"sourcePath": r"failed|\\server\share\secret.json"},
            {"sourcePath": r"prefix\\server\share\secret.json"},
            {"sourcePath": "//server/share/secret.json"},
            {"note": "../secret.json"},
            {"note": "prefix=../secret.json;suffix"},
            {"note": "prefix|../secret.json"},
            {"note": "safe/../../secret.json"},
            {"note": r"..\secret.json"},
            {"note": r"prefix=..\secret.json;suffix"},
            {"nested": {"result_artifact": {"mode": "create"}}},
            {"nested": [{"resultArtifact": {"path": "redacted"}}]},
            {"nested": {"RESULT-ARTIFACT": ["create", "redacted"]}},
            {"nested": {"result artifact": "redacted"}},
            {"nested": {"result.artifact": None}},
            {"nested": [{"ｒｅｓｕｌｔ＿ａｒｔｉｆａｃｔ": {"mode": "create"}}]},
            {"nested": [{"resuıt_artifact": {"mode": "create"}}]},
            {"nested": [{"result_artıfact": {"mode": "create"}}]},
        )
        for document in rejected_documents:
            with self.subTest(document=document):
                self._assert_document_rejects_for_both_kinds(document)

        safe_document = {
            "identity": "SSE:600519",
            "note": "ratio 1/2; docs https://example.com/a/b; C:relative",
            "escaped_text": r"literal \\ separator",
            "relative_reference": "notes/about",
            "query_locator": "https://example.test/a?next=/docs",
            "fragment_locator": "https://example.test/a#/docs",
        }
        candidate = prepare_candidate(
            canonical_security_id="SSE:600519",
            schema_version="1.0",
            document=safe_document,
        )
        self.assertIn(b"https://example.com/a/b", candidate.content_bytes)
        self.assertIn(b"https://example.test/a?next=/docs", candidate.content_bytes)
        self.assertIn(b"https://example.test/a#/docs", candidate.content_bytes)
        partition = prepare_completed_partition(
            kind="universe",
            exchange="SSE",
            shard_index=0,
            schema_version="1.0",
            records=(safe_document,),
        )
        self.assertIn(b"https://example.test/a?next=/docs", partition.content_bytes)
        self.assertIn(b"https://example.test/a#/docs", partition.content_bytes)

        for prepare in (
            lambda: prepare_candidate(
                canonical_security_id="SSE:600519",
                schema_version="1.0",
                document=_LeakingMapping({}),
            ),
            lambda: prepare_completed_partition(
                kind="universe",
                exchange="SSE",
                shard_index=0,
                schema_version="1.0",
                records=(_LeakingMapping({}),),
            ),
        ):
            with self.assertRaises(ValueError) as raised:
                prepare()
            self.assertNotIn("/private/tmp", str(raised.exception))
            self.assertNotIn("do-not-leak", str(raised.exception))

    def test_public_locator_span_never_absorbs_adjacent_local_path(self) -> None:
        unsafe_documents: tuple[Mapping[str, object], ...] = (
            {
                "note": (
                    "https://example.test/public),local=/private/synthetic/secret.json"
                )
            },
            {"note": "https://example.test/public)/private/synthetic/secret.json"},
            {"note": "https://example.test/public;/private/synthetic/secret.json"},
            {"note": "https://example.test/public,/private/synthetic/secret.json"},
            {
                "note": (
                    "https://example.test/public?next=/docs),"
                    "local=/private/synthetic/secret.json"
                )
            },
            {
                "note": (
                    "https://example.test/public#/docs);"
                    "local=/private/synthetic/secret.json"
                )
            },
        )
        for document in unsafe_documents:
            with self.subTest(document=document):
                self._assert_document_rejects_for_both_kinds(document)

        safe_document = {
            "query_locator": ("https://example.test/a?next=/docs&mode=full&ratio=1/2"),
            "fragment_locator": "https://example.test/a#/docs/section-2",
        }
        prepare_candidate(
            canonical_security_id="SSE:600519",
            schema_version="1.0",
            document=safe_document,
        )
        prepare_completed_partition(
            kind="ranking",
            exchange="BSE",
            shard_index=3,
            schema_version="1.0",
            records=(safe_document,),
        )

    def test_public_locators_reject_sensitive_or_local_authorities(self) -> None:
        unsafe_locators = (
            "https://user@example.test/public",
            "https://user:password@example.test/public",
            "https://example.test/public?access.tokens=redacted",
            "https://example.test/public?client-api-keys=redacted",
            "https://example.test/public#refresh/token=redacted",
            "https://example.test/public#section?authorization.cookie=redacted",
            "http://localhost/public",
            "http://worker.localhost/public",
            "http://127.0.0.1/public",
            "http://127.1/public",
            "http://2130706433/public",
            "http://167772161/public",
            "http://0x7f000001/public",
            "http://0177.0.0.1/public",
            "http://10.0.0.1/public",
            "http://172.16.0.1/public",
            "http://192.168.0.1/public",
            "http://[::1]/public",
            "http://[fc00::1]/public",
            "https://example.test:bad/public",
            "https://example.test:99999/public",
        )
        for locator in unsafe_locators:
            with self.subTest(locator=locator):
                self._assert_document_rejects_for_both_kinds(
                    {"public_reference": locator}
                )

        safe_document = {
            "query_locator": (
                "https://example.test/public?next=/docs&mode=full&ratio=1/2"
            ),
            "fragment_locator": (
                "https://research.example/public#/docs/section-2?mode=full"
            ),
            "named_port_locator": "http://public.example:8080/status",
        }
        candidate = prepare_candidate(
            canonical_security_id="SSE:600519",
            schema_version="1.0",
            document=safe_document,
        )
        self.assertIn(
            b"https://example.test/public?next=/docs", candidate.content_bytes
        )
        partition = prepare_completed_partition(
            kind="factors",
            exchange="BSE",
            shard_index=4,
            schema_version="1.0",
            records=(safe_document,),
        )
        self.assertIn(b"https://research.example/public#/docs", partition.content_bytes)

    def test_public_locators_require_positive_global_authorities(self) -> None:
        rejected_locators = (
            "https://research/public",
            "https://research.local/public",
            "https://research.internal/public",
            "https://localhost/public",
            "https://10.0.0.1/public",
            "https://127.0.0.1/public",
            "https://169.254.1.1/public",
            "https://100.64.0.1/public",
            "https://224.0.0.1/public",
            "https://240.0.0.1/public",
            "https://192.0.2.1/public",
            "https://[::1]/public",
            "https://[fc00::1]/public",
            "https://[fe80::1]/public",
            "https://[ff02::1]/public",
            "https://[2001:db8::1]/public",
            "https://127.0.0x1/public",
            "https://0x7f.0.0.1/public",
            "https://0x0a.0.0.1/public",
            "https://0177.0.0.1/public",
            "https://research.example../public",
            "https://research.example.../public",
            "https://[2606:4700:4700::1111%25eth0]/public",
        )
        for locator in rejected_locators:
            with self.subTest(locator=locator):
                self._assert_document_rejects_for_both_kinds(
                    {"public_reference": locator}
                )

        safe_document = {
            "test_domain": "https://example.test/public",
            "public_domain": "https://research.example/public",
            "rooted_public_domain": "https://research.example./public",
            "public_ipv4": "https://8.8.8.8/public",
            "public_ipv6": "https://[2606:4700:4700::1111]/public",
            "valid_port": "https://research.example:8080/public",
            "ordinary_string": "research.example is a public reference label",
        }
        candidate = prepare_candidate(
            canonical_security_id="SSE:600519",
            schema_version="1.0",
            document=safe_document,
        )
        partition = prepare_completed_partition(
            kind="ranking",
            exchange="SZSE",
            shard_index=10,
            schema_version="1.0",
            records=(safe_document,),
        )
        self.assertIn(b"https://example.test/public", candidate.content_bytes)
        self.assertIn(b"https://8.8.8.8/public", partition.content_bytes)

    def test_documents_obey_shared_public_security_policy(self) -> None:
        forbidden_keys = (
            "access_token",
            "credentials",
            "headers",
            "passwords",
            "provider_raw_payload",
            "raw_payload",
            "secrets",
            "server_metadata",
            "serverAddress",
            "server-url",
            "host",
            "hostname",
            "endpoint",
            "base_url",
            "_private",
            "",
            "非ASCII",
        )
        for key in forbidden_keys:
            with self.subTest(key=key):
                self._assert_document_rejects_for_both_kinds({key: "redacted"})

    def test_semantically_equivalent_sensitive_metadata_keys_are_rejected(
        self,
    ) -> None:
        forbidden_keys = (
            "AccessToken",
            "access/token",
            "authorizationHeader",
            "authorization+header",
            "providerRawPayload",
            "provider/raw/payload",
            "rawPayload",
            "raw+payload",
            "requestHeaders",
            "request/headers",
            "serverPort",
            "server/port",
            "serverName",
            "server+name",
            "serverIp",
            "server/ip",
        )
        for key in forbidden_keys:
            with self.subTest(key=key):
                self._assert_document_rejects_for_both_kinds({key: "redacted"})

        safe_document = {
            "provider_document_locator": "https://example.test/a?next=/docs",
            "request_status": "complete",
            "server_side_calculation": True,
        }
        prepare_candidate(
            canonical_security_id="SSE:600519",
            schema_version="1.0",
            document=safe_document,
        )
        prepare_completed_partition(
            kind="universe",
            exchange="SSE",
            shard_index=0,
            schema_version="1.0",
            records=(safe_document,),
        )

    def test_sensitive_key_equivalence_classes_fail_closed(self) -> None:
        forbidden_keys = (
            "provider/credentials",
            "account.passwords",
            "client+api+keys",
            "authorization/cookies",
            "session.secrets",
            "access-tokens",
            "refresh/tokens",
            "provider/raw/payloads",
            "raw.payloads",
            "request/header/maps",
            "qualified_request_headers",
            "server+addresses",
            "backup_server_info",
        )
        for key in forbidden_keys:
            with self.subTest(key=key):
                self._assert_document_rejects_for_both_kinds({key: "redacted"})

        safe_document = {
            "application_key_rotation_status": "not_applicable",
            "browser_storage_policy": "public-policy-v1",
            "raw_material_count": 2,
            "request_field_map": {"status": "complete"},
            "qualified_request_status": "complete",
            "backup_server_status": "healthy",
            "server_side_calculation": True,
            "tokenized_word_count": 3,
        }
        prepare_candidate(
            canonical_security_id="SSE:600519",
            schema_version="1.0",
            document=safe_document,
        )
        prepare_completed_partition(
            kind="ranking",
            exchange="SZSE",
            shard_index=9,
            schema_version="1.0",
            records=(safe_document,),
        )

    def test_sensitive_key_morphology_is_shared_with_locator_parameters(
        self,
    ) -> None:
        forbidden_keys = (
            "clientAPIKeys",
            "clientapikeys",
            "accountpassword",
            "providercredentials",
            "qualifiedrequestheaders",
            "backupproviderrawpayload",
            "backupserveraddress",
            "providerapikey",
        )
        for key in forbidden_keys:
            with self.subTest(key=key, surface="metadata"):
                self._assert_document_rejects_for_both_kinds({key: "redacted"})
            for locator in (
                f"https://example.test/public?{key}=redacted",
                f"https://example.test/public#{key}=redacted",
            ):
                with self.subTest(key=key, surface="locator", locator=locator):
                    self._assert_document_rejects_for_both_kinds(
                        {"public_reference": locator}
                    )

        safe_document = {
            "application_key_rotation_status": "not_applicable",
            "monkey_population": 12,
            "headerline_count": 3,
            "observerinfo_status": "complete",
            "server_side_calculation": True,
            "wordtoken_count": 4,
            "public_reference": (
                "https://example.test/public?"
                "application_key_rotation_status=not_applicable"
            ),
        }
        candidate = prepare_candidate(
            canonical_security_id="SSE:600519",
            schema_version="1.0",
            document=safe_document,
        )
        partition = prepare_completed_partition(
            kind="factors",
            exchange="BSE",
            shard_index=11,
            schema_version="1.0",
            records=(safe_document,),
        )
        self.assertIn(b"application_key_rotation_status", candidate.content_bytes)
        self.assertIn(b"monkey_population", partition.content_bytes)
        self.assertIn(b"observerinfo_status", partition.content_bytes)
        self.assertIn(b"wordtoken_count", candidate.content_bytes)

    def test_server_information_morphology_is_closed_across_surfaces(
        self,
    ) -> None:
        forbidden_keys = (
            "server_information",
            "server-informations",
            "serverInformation",
            "serverInformations",
            "backup_server_information",
            "backup_server_informations",
            "backupServerInformation",
            "backupServerInformations",
            "backupserverinformation",
            "backupserverinformations",
        )
        for key in forbidden_keys:
            with self.subTest(key=key, surface="metadata"):
                self._assert_document_rejects_for_both_kinds({key: "redacted"})
            for locator in (
                f"https://example.test/public?{key}=redacted",
                f"https://example.test/public#{key}=redacted",
            ):
                with self.subTest(key=key, surface="locator", locator=locator):
                    self._assert_document_rejects_for_both_kinds(
                        {"public_reference": locator}
                    )

    def test_runtime_resource_failures_are_not_relabelled_as_input_errors(
        self,
    ) -> None:
        calls = (
            (
                "result artifact candidate preparation exhausted memory",
                lambda: prepare_candidate(
                    canonical_security_id="SSE:600519",
                    schema_version="1.0",
                    document=_ExhaustedMapping({}),
                ),
            ),
            (
                "result artifact partition preparation exhausted memory",
                lambda: prepare_completed_partition(
                    kind="universe",
                    exchange="SSE",
                    shard_index=0,
                    schema_version="1.0",
                    records=(_ExhaustedMapping({}),),
                ),
            ),
        )
        for expected_message, call in calls:
            with self.subTest(expected_message=expected_message):
                with self.assertRaises(MemoryError) as raised:
                    call()
                self.assertEqual(str(raised.exception), expected_message)
                self.assertNotIn("/private", str(raised.exception))
                self.assertNotIn("secret", str(raised.exception))
                self.assertIsNone(raised.exception.__cause__)
                self.assertTrue(raised.exception.__suppress_context__)

    def test_unexpected_ordinary_exceptions_are_sanitized_for_both_kinds(
        self,
    ) -> None:
        calls = (
            (
                "result artifact candidate document is invalid",
                lambda: prepare_candidate(
                    canonical_security_id="SSE:600519",
                    schema_version="1.0",
                    document=_RuntimeLeakingMapping({}),
                ),
            ),
            (
                "result artifact partition records are invalid",
                lambda: prepare_completed_partition(
                    kind="universe",
                    exchange="SSE",
                    shard_index=0,
                    schema_version="1.0",
                    records=(_RuntimeLeakingMapping({}),),
                ),
            ),
        )
        for expected_message, call in calls:
            with self.subTest(expected_message=expected_message):
                with self.assertRaises(ValueError) as raised:
                    call()
                self.assertEqual(str(raised.exception), expected_message)
                self.assertNotIn("/private", str(raised.exception))
                self.assertNotIn("do-not-leak", str(raised.exception))

    def test_input_kind_observation_exceptions_are_sanitized_for_both_kinds(
        self,
    ) -> None:
        hostile_input = _RuntimeLeakingClassSequence()
        calls = (
            (
                "result artifact candidate document is invalid",
                lambda: prepare_candidate(
                    canonical_security_id="SSE:600519",
                    schema_version="1.0",
                    document=hostile_input,  # type: ignore[arg-type]
                ),
            ),
            (
                "result artifact partition records are invalid",
                lambda: prepare_completed_partition(
                    kind="universe",
                    exchange="SSE",
                    shard_index=0,
                    schema_version="1.0",
                    records=hostile_input,  # type: ignore[arg-type]
                ),
            ),
        )
        for expected_message, call in calls:
            with self.subTest(expected_message=expected_message):
                with self.assertRaises(ValueError) as raised:
                    call()
                self.assertEqual(str(raised.exception), expected_message)
                self.assertNotIn("/private", str(raised.exception))
                self.assertNotIn("do-not-leak", str(raised.exception))

    def _assert_document_rejects_for_both_kinds(
        self,
        document: Mapping[str, object],
    ) -> None:
        calls = (
            lambda: prepare_candidate(
                canonical_security_id="SSE:600519",
                schema_version="1.0",
                document=document,
            ),
            lambda: prepare_completed_partition(
                kind="universe",
                exchange="SSE",
                shard_index=0,
                schema_version="1.0",
                records=(document,),
            ),
        )
        for call in calls:
            with self.assertRaises(ValueError) as raised:
                call()
            self.assertNotIn("/private/tmp", str(raised.exception))
            self.assertNotIn("secret", str(raised.exception))

    def test_json_data_and_structural_bounds_fail_closed(self) -> None:
        too_deep: dict[str, object] = {}
        cursor = too_deep
        for _ in range(33):
            nested: dict[str, object] = {}
            cursor["nested"] = nested
            cursor = nested

        cyclic: list[object] = []
        cyclic.append(cyclic)
        invalid_documents: tuple[Mapping[str, object], ...] = (
            too_deep,
            {"items": [None] * 10_000},
            {"k" * 129: "value"},
            {"value": "x" * (64 * 1024 + 1)},
            {"value": float("nan")},
            {"value": float("inf")},
            {"value": float("-inf")},
            {"value": Decimal("NaN")},
            {"value": Decimal("Infinity")},
            {"value": b"not-json"},
            {"value": cyclic},
        )
        for document in invalid_documents:
            with self.subTest(document_type=type(next(iter(document.values())))):
                self._assert_document_rejects_for_both_kinds(document)

        with self.assertRaises(ValueError):
            prepare_completed_partition(
                kind="universe",
                exchange="SSE",
                shard_index=0,
                schema_version="1.0",
                records=_ManyRecords(),  # type: ignore[arg-type]
            )

        decimal_document = {
            "negative_zero": Decimal("-0"),
            "ratio": Decimal("1.2300"),
        }
        first = prepare_candidate(
            canonical_security_id="SZSE:000001",
            schema_version="candidate@1.0",
            document=decimal_document,
        )
        second = prepare_candidate(
            canonical_security_id="SZSE:000001",
            schema_version="candidate@1.0",
            document=decimal_document,
        )
        self.assertEqual(
            first.content_bytes,
            b'{"negative_zero":"-0","ratio":"1.2300"}\n',
        )
        self.assertEqual(first, second)

    def test_candidate_and_partition_have_exact_byte_bounds(self) -> None:
        repeated_value = "x" * (64 * 1024)
        exact_limit_document = {"values": [repeated_value] * 1_023 + ["x" * 62_451]}
        candidate = prepare_candidate(
            canonical_security_id="SSE:600519",
            schema_version="1.0",
            document=exact_limit_document,
        )
        self.assertEqual(candidate.byte_count, 64 * 1024 * 1024)
        del candidate

        partition = prepare_completed_partition(
            kind="factors",
            exchange="SSE",
            shard_index=0,
            schema_version="1.0",
            records=(exact_limit_document,),
        )
        self.assertEqual(partition.byte_count, 64 * 1024 * 1024)
        del partition

        over_limit_document = {"values": [repeated_value] * 1_023 + ["x" * 62_452]}
        with self.assertRaises(ValueError):
            prepare_candidate(
                canonical_security_id="SSE:600519",
                schema_version="1.0",
                document=over_limit_document,
            )
        with self.assertRaises(ValueError):
            prepare_completed_partition(
                kind="factors",
                exchange="SSE",
                shard_index=0,
                schema_version="1.0",
                records=(over_limit_document,),
            )

    def test_module_interface_owns_only_safe_relative_coordinates(self) -> None:
        self.assertEqual(
            set(result_artifact_inputs_module.__all__),
            {
                "PreparedResultArtifactFile",
                "prepare_candidate",
                "prepare_completed_partition",
            },
        )
        prepared_files = [
            prepare_candidate(
                canonical_security_id=f"{exchange}:000001",
                schema_version="1.0",
                document={},
            )
            for exchange in ("BSE", "SSE", "SZSE")
        ]
        prepared_files.extend(
            prepare_completed_partition(
                kind=kind,
                exchange=exchange,
                shard_index=shard_index,
                schema_version="1.0",
                records=(),
            )
            for kind in ("universe", "factors", "ranking")
            for exchange in ("BSE", "SSE", "SZSE")
            for shard_index in (0, 99_999)
        )
        for prepared in prepared_files:
            with self.subTest(relative_path=prepared.relative_path):
                self.assertFalse(prepared.relative_path.startswith("/"))
                self.assertNotIn("\\", prepared.relative_path)
                self.assertNotIn("..", prepared.relative_path.split("/"))
                self.assertNotIn(":", prepared.relative_path)

    def test_preparation_has_no_caller_directory_side_effect(self) -> None:
        original_directory = os.getcwd()
        with tempfile.TemporaryDirectory() as caller_directory:
            os.chdir(caller_directory)
            try:
                prepare_candidate(
                    canonical_security_id="SSE:600519",
                    schema_version="1.0",
                    document={"status": "limited"},
                )
                prepare_completed_partition(
                    kind="universe",
                    exchange="SSE",
                    shard_index=0,
                    schema_version="1.0",
                    records=({"status": "limited"},),
                )
                self.assertTrue(os.path.samefile(os.getcwd(), caller_directory))
                self.assertEqual(os.listdir(caller_directory), [])
            finally:
                os.chdir(original_directory)


class ResultArtifactSemanticKeyTests(unittest.TestCase):
    def _factories(self) -> tuple[tuple[str, PreparedFactory], ...]:
        return (
            (
                "candidate",
                lambda document: prepare_candidate(
                    canonical_security_id="SSE:600519",
                    schema_version="1.0",
                    document=document,
                ),
            ),
            (
                "partition",
                lambda document: prepare_completed_partition(
                    kind="universe",
                    exchange="SSE",
                    shard_index=0,
                    schema_version="1.0",
                    records=(document,),
                ),
            ),
        )

    def _surface_document(self, surface: str, raw_key: str) -> Mapping[str, object]:
        if surface == "metadata":
            return {raw_key: "redacted"}
        if surface == "query":
            return {
                "public_reference": (
                    f"https://research.example/public?{raw_key}=redacted"
                )
            }
        if surface == "fragment":
            return {
                "public_reference": (
                    f"https://research.example/public#{raw_key}=redacted"
                )
            }
        raise AssertionError(f"unknown test surface: {surface}")

    def test_complete_semantic_key_rejects_cross_token_morphology_everywhere(
        self,
    ) -> None:
        sensitive_keys = (
            "jobserverInformationStatus",
            "jobserver_info_status",
            "backupserver-information",
            "clientapiKeys",
            "proxyrequestHeaders",
            "thirdPartyProviderRawPayloadsDigest",
            "account.credentials.rotation",
            "applicationPasswordStatus",
        )

        for raw_key in sensitive_keys:
            for surface in ("metadata", "query", "fragment"):
                document = self._surface_document(surface, raw_key)
                for factory_name, factory in self._factories():
                    with self.subTest(
                        raw_key=raw_key,
                        surface=surface,
                        factory=factory_name,
                    ):
                        with self.assertRaisesRegex(
                            ValueError,
                            "^result artifact .+ invalid$",
                        ):
                            factory(document)

    def test_complete_key_morphology_cross_product_uses_one_shared_result(
        self,
    ) -> None:
        qualifiers = ("job", "backup", "client", "proxy", "thirdParty", "x")
        sensitive_roots = (
            "server_info",
            "server-information",
            "serverInformation",
            "apiKeys",
            "request_headers",
            "requestHeaders",
            "providerRawPayloads",
            "credentials",
            "passwords",
            "accessTokens",
        )
        suffixes = ("status", "digest", "backup", "rotation", "v2")
        separators = ("", "_", "-", ".")

        for qualifier, root, suffix, separator in product(
            qualifiers,
            sensitive_roots,
            suffixes,
            separators,
        ):
            raw_key = separator.join((qualifier, root, suffix))
            for surface in ("metadata", "query", "fragment"):
                document = self._surface_document(surface, raw_key)
                for factory_name, factory in self._factories():
                    with self.subTest(
                        raw_key=raw_key,
                        surface=surface,
                        factory=factory_name,
                    ):
                        with self.assertRaises(ValueError):
                            factory(document)

    def test_exact_safe_whole_lexemes_do_not_exempt_sensitive_compounds(
        self,
    ) -> None:
        safe_keys = (
            "key",
            "header",
            "server",
            "application",
            "rotation",
            "application_key_rotation_status",
            "observer_information_status",
        )

        for raw_key in safe_keys:
            for surface in ("metadata", "query", "fragment"):
                document = self._surface_document(surface, raw_key)
                for factory_name, factory in self._factories():
                    with self.subTest(
                        raw_key=raw_key,
                        surface=surface,
                        factory=factory_name,
                    ):
                        prepared = factory(document)
                        self.assertIn(raw_key.encode(), prepared.content_bytes)

        for raw_key in (
            "serverInformation",
            "requestHeaders",
            "apiKeys",
            "credentialRotation",
            "passwordStatus",
        ):
            for surface in ("metadata", "query", "fragment"):
                document = self._surface_document(surface, raw_key)
                for factory_name, factory in self._factories():
                    with self.subTest(
                        raw_key=raw_key,
                        surface=surface,
                        factory=factory_name,
                    ):
                        with self.assertRaises(ValueError):
                            factory(document)


class ResultArtifactLocatorTests(unittest.TestCase):
    def _factories(self) -> tuple[tuple[str, PreparedFactory], ...]:
        return (
            (
                "candidate",
                lambda document: prepare_candidate(
                    canonical_security_id="SSE:600519",
                    schema_version="1.0",
                    document=document,
                ),
            ),
            (
                "partition",
                lambda document: prepare_completed_partition(
                    kind="factors",
                    exchange="BSE",
                    shard_index=9,
                    schema_version="1.0",
                    records=(document,),
                ),
            ),
        )

    def test_complete_http_authority_matrix_is_positive_and_fail_closed(
        self,
    ) -> None:
        safe_locators = (
            "https://example.test/public",
            "https://research.example./public",
            "https://8.8.8.8/public",
            "https://[2606:4700:4700::1111]/public",
            "http://public.example:8080/status",
            "https://example.test/public?next=/docs&mode=full&ratio=1/2",
            "https://research.example/public#/docs/section-2?mode=full",
        )
        for locator in safe_locators:
            for factory_name, factory in self._factories():
                with self.subTest(
                    locator=locator,
                    factory=factory_name,
                    expected="accepted",
                ):
                    prepared = factory({"public_reference": locator})
                    self.assertIn(locator.encode(), prepared.content_bytes)

        rejected_locators = (
            "https://user@example.test/public",
            "https://user:password@example.test/public",
            "https://example.test:/public",
            "https://example.test:0/public",
            "https://example.test:bad/public",
            "https://example.test:65536/public",
            "https://research/public",
            "https://research.local/public",
            "https://research.internal/public",
            "https://localhost/public",
            "https://10.0.0.1/public",
            "https://127.0.0.1/public",
            "https://169.254.1.1/public",
            "https://100.64.0.1/public",
            "https://224.0.0.1/public",
            "https://240.0.0.1/public",
            "https://192.0.2.1/public",
            "https://[::1]/public",
            "https://[fc00::1]/public",
            "https://[fe80::1]/public",
            "https://[ff02::1]/public",
            "https://[2001:db8::1]/public",
            "https://127.1/public",
            "https://2130706433/public",
            "https://0x7f000001/public",
            "https://0177.0.0.1/public",
            "https://127.0.0x1/public",
            "https://0x7f.0.0.1/public",
            "https://research.example../public",
            "https://[2606:4700:4700::1111%25eth0]/public",
            "https://example.test/public?clientapiKeys=redacted",
            "https://example.test/public#proxyrequestHeaders=redacted",
        )
        for locator in rejected_locators:
            for factory_name, factory in self._factories():
                with self.subTest(
                    locator=locator,
                    factory=factory_name,
                    expected="rejected",
                ):
                    with self.assertRaises(ValueError):
                        factory({"public_reference": locator})

    def test_parser_significant_authority_continuations_and_c0_fail_closed(
        self,
    ) -> None:
        continuations = (
            "https://example.test;@localhost/private",
            "https://example.test,@localhost/private",
            "https://example.test(@localhost/private",
            "https://example.test^@localhost/private",
            "https://example.test`@localhost/private",
            "https://example.test{@localhost/private",
            "https://example.test}@localhost/private",
            "https://example.test|@localhost/private",
            'https://example.test"@localhost/private',
            "https://example.test'@localhost/private",
            "https://example.test<@localhost/private",
            "https://example.test>@localhost/private",
            r"https://example.test\@localhost/private",
            "https://example.test/public),local=/private/synthetic/secret.json",
            "https://example.test/public;/private/synthetic/secret.json",
        )
        for locator in continuations:
            for factory_name, factory in self._factories():
                with self.subTest(locator=locator, factory=factory_name):
                    with self.assertRaises(ValueError):
                        factory({"public_reference": locator})

        for code_point in (*range(0x20), 0x7F):
            locator = f"https://example.test{chr(code_point)}@localhost/private"
            for factory_name, factory in self._factories():
                with self.subTest(code_point=code_point, factory=factory_name):
                    with self.assertRaises(ValueError):
                        factory({"public_reference": locator})


if __name__ == "__main__":
    unittest.main()
