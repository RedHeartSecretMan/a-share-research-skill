"""Journalled, write-once Result Artifact lifecycle on a local filesystem.

A transaction publishes its complete immutable journal before publishing content.
Completed journals are durable ownership records, not disposable recovery debris.
The checkpoint and manifest contain only canonical public metadata; physical
identities and staging coordinates stay inside the Adapter's protocol records.
"""

from __future__ import annotations

import ctypes
import errno
import json
import os
import secrets
import stat
import sys
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Protocol, cast

from .result_artifact_inputs import PreparedResultArtifactFile

try:
    import fcntl
except ImportError:  # pragma: no cover - production fails closed on this platform
    fcntl = None  # type: ignore[assignment]

_MAX_CHECKPOINT_BYTES = 65_536
_MAX_MANIFEST_BYTES = 65_536
_MAX_FILE_BYTES = 64 * 1024 * 1024
_MAX_RECEIPT_BYTES = 1024
_MAX_IDENTITY = (1 << 64) - 1
_PROTOCOL = ".protocol"
_EXCHANGES = ("BSE", "SSE", "SZSE")
_COVERAGE_KEYS = frozenset(
    {"acquired", "expected", "failed", "ranked", "resolved", "unranked", "unsupported"}
)
_FIXED_DIRECTORIES = tuple(
    sorted(
        {
            _PROTOCOL,
            "candidates",
            "partitions",
            *(f"partitions/{kind}" for kind in ("universe", "factors", "ranking")),
            *(
                f"partitions/{kind}/{exchange}"
                for kind in ("universe", "factors", "ranking")
                for exchange in _EXCHANGES
            ),
        }
    )
)
_USE_ANCHORED_DIRECTORY_FD = (
    os.name == "posix"
    and fcntl is not None
    and hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "O_DIRECTORY")
    and all(
        function in os.supports_dir_fd
        for function in (
            os.open,
            os.mkdir,
            os.stat,
            os.link,
            os.rename,
            os.rmdir,
            os.unlink,
        )
    )
    and os.listdir in os.supports_fd
    and os.stat in os.supports_follow_symlinks
    and os.link in os.supports_follow_symlinks
)
_FileIdentity = tuple[int, int]


def _json(value: object) -> bytes:
    try:
        return (
            json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
    except (RecursionError, TypeError, UnicodeError, ValueError):
        raise ValueError("result artifact metadata is invalid") from None


def _object(encoded: bytes, maximum: int = _MAX_MANIFEST_BYTES) -> dict[str, object]:
    if len(encoded) > maximum:
        raise ValueError("result artifact metadata exceeds its bounded size")
    try:
        value: object = json.loads(encoded)
        if type(value) is not dict or _json(value) != encoded:
            raise ValueError("metadata is not canonical")
    except (RecursionError, TypeError, UnicodeError, ValueError):
        raise ValueError("result artifact metadata is invalid") from None
    return cast(dict[str, object], value)


def _digest(encoded: bytes) -> str:
    return sha256(encoded).hexdigest()


def _identifier(value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise ValueError("result artifact ownership is invalid")
    return value


def _hex_identity(value: _FileIdentity) -> list[str]:
    if any(type(n) is not int or not 0 <= n <= _MAX_IDENTITY for n in value):
        raise ValueError("result artifact identity is unsupported")
    return [f"{n:016x}" for n in value]


def _parse_identity(value: object) -> _FileIdentity:
    if (
        type(value) is not list
        or len(value) != 2
        or any(
            type(n) is not str
            or len(n) != 16
            or any(c not in "0123456789abcdef" for c in n)
            for n in value
        )
    ):
        raise ValueError("result artifact ownership identity is invalid")
    return int(value[0], 16), int(value[1], 16)


def _path(path: object) -> Path:
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or not path.name
        or ".." in path.parts
        or "\x00" in str(path)
    ):
        raise ValueError("result artifact path is unsafe or unavailable")
    return path


@dataclass(frozen=True, slots=True)
class ResultArtifactIdentity:
    """Exact execution bytes, including frozen context in the plan or request."""

    artifact_schema_version: str
    request_bytes: bytes
    producer_bytes: bytes
    plan_bytes: bytes
    operation_manifest_bytes: bytes

    def __post_init__(self) -> None:
        version = self.artifact_schema_version
        if (
            type(version) is not str
            or not 1 <= len(version.encode("utf-8")) <= 128
            or any(
                c
                not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._@+-"
                for c in version
            )
        ):
            raise ValueError("result artifact schema version is invalid")
        for value in (
            self.request_bytes,
            self.producer_bytes,
            self.plan_bytes,
            self.operation_manifest_bytes,
        ):
            if type(value) is not bytes or not value:
                raise ValueError("result artifact identity bytes must be non-empty")

    def digests(self) -> dict[str, str]:
        return {
            "request_sha256": _digest(self.request_bytes),
            "producer_sha256": _digest(self.producer_bytes),
            "plan_sha256": _digest(self.plan_bytes),
            "operation_manifest_sha256": _digest(self.operation_manifest_bytes),
        }

    @property
    def artifact_id(self) -> str:
        payload = {
            "artifact_schema_version": self.artifact_schema_version,
            **self.digests(),
        }
        return "artifact-" + _digest(_json(payload)[:-1])[:24]


@dataclass(frozen=True, slots=True)
class _File:
    relative_path: str
    schema_version: str
    sha256: str
    byte_count: int
    record_count: int

    def payload(self) -> dict[str, object]:
        return {
            "relative_path": self.relative_path,
            "schema_version": self.schema_version,
            "sha256": self.sha256,
            "byte_count": self.byte_count,
            "record_count": self.record_count,
        }


def _consume(value: PreparedResultArtifactFile, kind: str) -> tuple[_File, bytes]:
    # #115's exported capability owns all caller value/coordinate validation.
    if not isinstance(value, PreparedResultArtifactFile):
        raise ValueError("result artifact prepared file is invalid")
    path, version, encoded = (
        value.relative_path,
        value.schema_version,
        value.content_bytes,
    )
    digest, size, count = value.sha256, value.byte_count, value.record_count
    if not path.startswith("partitions/" if kind == "partition" else "candidates/"):
        raise ValueError("result artifact prepared file kind is invalid")
    return _File(path, version, digest, size, count), encoded


def _context_input(value: PreparedResultArtifactFile | None) -> bytes | None:
    if value is None:
        return None
    if not isinstance(value, PreparedResultArtifactFile):
        raise ValueError("result artifact prepared context is invalid")
    # Consume the sealed factory's content/count authority once. Its coordinate
    # is a carrier only: it is never published or declared a completed shard.
    encoded, count = value.content_bytes, value.record_count
    if count != 1:
        raise ValueError("result artifact context must contain one frozen record")
    return encoded


def _identity_digests(
    identity: ResultArtifactIdentity, context: bytes | None
) -> dict[str, str]:
    values = identity.digests()
    if context is not None:
        values["context_sha256"] = _digest(context)
    return values


def _artifact_id(identity: ResultArtifactIdentity, context: bytes | None = None) -> str:
    if context is None:
        return identity.artifact_id
    return (
        "artifact-"
        + _digest(
            _json(
                {
                    "artifact_schema_version": identity.artifact_schema_version,
                    **_identity_digests(identity, context),
                }
            )[:-1]
        )[:24]
    )


def _stored_file(value: object) -> _File:
    if type(value) is not dict or set(value) != {
        "relative_path",
        "schema_version",
        "sha256",
        "byte_count",
        "record_count",
    }:
        raise ValueError("result artifact file record is invalid")
    path, version = value["relative_path"], value["schema_version"]
    size, count = value["byte_count"], value["record_count"]
    if (
        type(path) is not str
        or type(version) is not str
        or not 1 <= len(version) <= 128
    ):
        raise ValueError("result artifact file record is invalid")
    parts = PurePosixPath(path).parts
    partition = (
        len(parts) == 4
        and parts[0] == "partitions"
        and parts[1] in {"universe", "factors", "ranking"}
        and parts[2] in _EXCHANGES
        and len(parts[3]) == 16
        and parts[3].startswith("part-")
        and parts[3][5:10].isdigit()
        and parts[3][10:] == ".jsonl"
    )
    candidate = (
        len(parts) == 2
        and parts[0] == "candidates"
        and len(parts[1]) in {15, 16}
        and parts[1].endswith(".json")
        and parts[1].split("-")[0] in _EXCHANGES
        and parts[1].split("-")[-1][:-5].isdigit()
        and len(parts[1].split("-")[-1][:-5]) == 6
    )
    if not (partition or candidate) or PurePosixPath(path).as_posix() != path:
        raise ValueError("result artifact file coordinate is invalid")
    if (
        type(size) is not int
        or not 0 <= size <= _MAX_FILE_BYTES
        or type(count) is not int
        or not 0 <= count <= 100_000
    ):
        raise ValueError("result artifact file bounds are invalid")
    if candidate and count != 1:
        raise ValueError("result artifact candidate count is invalid")
    return _File(path, version, _identifier(value["sha256"]), size, count)


def _checkpoint(
    identity: ResultArtifactIdentity,
    files: Sequence[_File] = (),
    context: bytes | None = None,
) -> bytes:
    encoded = _json(
        {
            "artifact_id": _artifact_id(identity, context),
            "artifact_schema_version": identity.artifact_schema_version,
            "identity": _identity_digests(identity, context),
            "status": "partial",
            "pending_files": [],
            "frozen_context": json.loads(context) if context is not None else None,
            "completed_files": [
                f.payload() for f in sorted(files, key=lambda item: item.relative_path)
            ],
        }
    )
    if len(encoded) > _MAX_CHECKPOINT_BYTES:
        raise ValueError("result artifact checkpoint exceeds its bounded size")
    return encoded


def _coverage(value: object) -> dict[str, dict[str, int]]:
    if not isinstance(value, Mapping) or set(value) != set(_EXCHANGES):
        raise ValueError("result artifact coverage is invalid")
    result: dict[str, dict[str, int]] = {}
    for exchange in _EXCHANGES:
        counts = value[exchange]
        if (
            not isinstance(counts, Mapping)
            or set(counts) != _COVERAGE_KEYS
            or any(type(n) is not int or n < 0 for n in counts.values())
        ):
            raise ValueError("result artifact coverage is invalid")
        result[exchange] = {
            key: cast(int, counts[key]) for key in sorted(_COVERAGE_KEYS)
        }
    return result


def _manifest(
    identity: ResultArtifactIdentity,
    files: Sequence[_File],
    coverage: object,
    context: bytes | None = None,
) -> bytes:
    encoded = _json(
        {
            "artifact_id": _artifact_id(identity, context),
            "artifact_schema_version": identity.artifact_schema_version,
            "identity": _identity_digests(identity, context),
            "status": "finalized",
            "coverage": _coverage(coverage),
            "files": [
                f.payload() for f in sorted(files, key=lambda item: item.relative_path)
            ],
            "totals": {
                "file_count": len(files),
                "record_count": sum(f.record_count for f in files),
            },
        }
    )
    if len(encoded) > _MAX_MANIFEST_BYTES:
        raise ValueError("result artifact manifest exceeds its bounded size")
    return encoded


def _receipt(
    identity: ResultArtifactIdentity,
    files: Sequence[_File],
    manifest: bytes,
    context: bytes | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "artifact_id": _artifact_id(identity, context),
        "artifact_schema_version": identity.artifact_schema_version,
        "status": "finalized",
        "file_count": len(files),
        "record_count": sum(f.record_count for f in files),
        "manifest_sha256": _digest(manifest),
    }
    if len(_json(value)[:-1]) > _MAX_RECEIPT_BYTES:
        raise ValueError("result artifact receipt exceeds its bounded size")
    return value


@dataclass(frozen=True, slots=True)
class _Node:
    relative_path: str
    identity: _FileIdentity
    kind: str
    encoded: bytes = b""


@dataclass(frozen=True, slots=True)
class _Snapshot:
    identity: _FileIdentity
    nodes: tuple[_Node, ...]

    def mapping(self) -> dict[str, _Node]:
        return {n.relative_path: n for n in self.nodes}


@dataclass(frozen=True, slots=True)
class _Stage:
    token: str
    identity: _FileIdentity
    files: tuple[tuple[str, _FileIdentity], ...]
    journal_bytes: bytes = b""
    expected_nodes: tuple[_Node, ...] | None = None

    def identities(self) -> dict[str, _FileIdentity]:
        return dict(self.files)


class _Store(Protocol):
    root_identity: _FileIdentity
    ancestor_identities: tuple[_FileIdentity, ...]

    def snapshot(self) -> _Snapshot: ...
    def stage_snapshot(self, token: str) -> _Snapshot | None: ...
    def prepare(
        self,
        token: str,
        data: bytes,
        checkpoint: bytes | None,
        journal: Callable[[_Stage], bytes],
    ) -> _Stage: ...
    def publish_journal(self, stage: _Stage, relative_path: str) -> None: ...
    def publish_file(self, stage: _Stage, relative_path: str) -> None: ...
    def replace_checkpoint(self, stage: _Stage, previous: _Node) -> None: ...
    def cleanup(self, stage: _Stage, observed: _Snapshot) -> None: ...
    def sync(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _Transaction:
    name: str
    stage: _Stage
    file: _File | None
    previous_checkpoint: _FileIdentity
    next_checkpoint: _FileIdentity | None
    next_checkpoint_digest: str | None
    encoded: bytes


@dataclass(frozen=True, slots=True)
class _Observation:
    snapshot: _Snapshot
    stages: tuple[tuple[str, _Snapshot | None], ...]
    files: tuple[_File, ...]
    context: bytes | None
    token: str
    transactions: tuple[_Transaction, ...]
    pending: bool
    manifest_bytes: bytes | None


def _creation_ownership(
    identity: ResultArtifactIdentity,
    context: bytes | None,
    token: str,
    root_id: _FileIdentity,
    owner_id: _FileIdentity,
    directories: Mapping[str, _FileIdentity],
    cp_id: _FileIdentity,
    initial: bytes,
    ancestors: tuple[_FileIdentity, ...],
) -> bytes:
    encoded = _json(
        {
            "type": "creation",
            "token": token,
            "artifact_id": _artifact_id(identity, context),
            "root_identity": _hex_identity(root_id),
            "owner_identity": _hex_identity(owner_id),
            "ancestor_identities": [_hex_identity(item) for item in ancestors],
            "directories": {
                name: _hex_identity(value) for name, value in directories.items()
            },
            "checkpoint_identity": _hex_identity(cp_id),
            "checkpoint_sha256": _digest(initial),
        }
    )
    if len(encoded) > _MAX_MANIFEST_BYTES:
        raise ValueError("result artifact ownership exceeds its bounded size")
    return encoded


class _TransactionalAdapter:
    """One lifecycle transaction Interface shared by both storage Adapters."""

    def _phase(self, phase: str) -> None:
        pass

    @contextmanager
    def _session(self, path: Path, expected: _FileIdentity | None) -> Iterator[_Store]:
        raise NotImplementedError
        yield  # pragma: no cover

    def create(
        self, path: Path, identity: ResultArtifactIdentity, context: bytes | None = None
    ) -> _FileIdentity:
        raise NotImplementedError

    def _observe(self, store: _Store, identity: ResultArtifactIdentity) -> _Observation:
        snapshot = store.snapshot()
        nodes = snapshot.mapping()
        cp = nodes.get("checkpoint.json")
        if cp is None or cp.kind != "file":
            raise ValueError("result artifact checkpoint is missing")
        checkpoint_payload = _object(cp.encoded, _MAX_CHECKPOINT_BYTES)
        frozen = checkpoint_payload.get("frozen_context")
        if frozen is not None and type(frozen) is not dict:
            raise ValueError("result artifact frozen context is invalid")
        context = _json(frozen) if frozen is not None else None
        owners = [
            n for n in snapshot.nodes if n.relative_path.startswith(".protocol/create-")
        ]
        if len(owners) != 1 or owners[0].kind != "file":
            raise ValueError("result artifact creation ownership is invalid")
        owner_node = owners[0]
        owner = _object(owner_node.encoded)
        if set(owner) != {
            "type",
            "token",
            "artifact_id",
            "root_identity",
            "owner_identity",
            "ancestor_identities",
            "directories",
            "checkpoint_identity",
            "checkpoint_sha256",
        }:
            raise ValueError("result artifact creation ownership is invalid")
        token = _identifier(owner["token"])
        if (
            owner_node.relative_path != f".protocol/create-{token}.json"
            or owner["type"] != "creation"
            or owner["artifact_id"] != _artifact_id(identity, context)
        ):
            raise ValueError(
                "result artifact creation ownership does not match execution"
            )
        if (
            _parse_identity(owner["root_identity"]) != snapshot.identity
            or _parse_identity(owner["owner_identity"]) != owner_node.identity
        ):
            raise ValueError("result artifact creation ownership identity changed")
        ancestors = owner["ancestor_identities"]
        if (
            type(ancestors) is not list
            or tuple(_parse_identity(item) for item in ancestors)
            != store.ancestor_identities
        ):
            raise ValueError("result artifact ancestor generation changed")
        directory_payload = owner["directories"]
        if type(directory_payload) is not dict or set(directory_payload) != set(
            _FIXED_DIRECTORIES
        ):
            raise ValueError("result artifact directory ownership is invalid")
        for name in _FIXED_DIRECTORIES:
            if (
                name not in nodes
                or nodes[name].kind != "directory"
                or nodes[name].identity != _parse_identity(directory_payload[name])
            ):
                raise ValueError("result artifact directory ownership identity changed")
        current_id = _parse_identity(owner["checkpoint_identity"])
        current_digest = _identifier(owner["checkpoint_sha256"])
        if current_digest != _digest(_checkpoint(identity, context=context)):
            raise ValueError(
                "result artifact initial checkpoint does not match execution"
            )
        expected_names = {
            *_FIXED_DIRECTORIES,
            owner_node.relative_path,
            "checkpoint.json",
        }
        files: list[_File] = []
        transactions: list[_Transaction] = []
        stages: list[tuple[str, _Snapshot | None]] = []
        pending = False
        manifest_bytes: bytes | None = None
        journal_nodes = sorted(
            (n for n in snapshot.nodes if n.relative_path.startswith(".protocol/txn-")),
            key=lambda n: n.relative_path,
        )
        for sequence, node in enumerate(journal_nodes, 1):
            if node.kind != "file":
                raise ValueError("result artifact publication journal is invalid")
            journal = _object(node.encoded)
            if set(journal) != {
                "type",
                "token",
                "creation_token",
                "root_identity",
                "sequence",
                "journal_identity",
                "stage_identity",
                "stage_files",
                "old_checkpoint_identity",
                "old_checkpoint_sha256",
                "new_checkpoint_identity",
                "new_checkpoint_sha256",
                "file",
                "coverage",
            }:
                raise ValueError("result artifact publication journal is invalid")
            tx_token = _identifier(journal["token"])
            name = f".protocol/txn-{sequence:06d}-{tx_token}.json"
            if (
                node.relative_path != name
                or journal["type"] != "publication"
                or journal["creation_token"] != token
                # JSON booleans and floats must not impersonate integer lineage.
                or type(journal["sequence"]) is not int
                or journal["sequence"] != sequence
            ):
                raise ValueError(
                    "result artifact publication journal lineage is invalid"
                )
            if (
                _parse_identity(journal["root_identity"]) != snapshot.identity
                or _parse_identity(journal["journal_identity"]) != node.identity
            ):
                raise ValueError("result artifact publication journal identity changed")
            if (
                _parse_identity(journal["old_checkpoint_identity"]) != current_id
                or journal["old_checkpoint_sha256"] != current_digest
            ):
                raise ValueError(
                    "result artifact publication journal predecessor changed"
                )
            staged_values = journal["stage_files"]
            if type(staged_values) is not dict:
                raise ValueError("result artifact staged ownership is invalid")
            file = None if journal["file"] is None else _stored_file(journal["file"])
            is_last = sequence == len(journal_nodes)
            required_stage_names = {"data", "journal"} | (
                {"checkpoint"} if file is not None else set()
            )
            if set(staged_values) != required_stage_names:
                raise ValueError("result artifact staged ownership is invalid")
            stage_ids = {
                key: _parse_identity(value) for key, value in staged_values.items()
            }
            if stage_ids["journal"] != node.identity:
                raise ValueError("result artifact staged journal identity changed")
            stage = _Stage(
                tx_token,
                _parse_identity(journal["stage_identity"]),
                tuple(sorted(stage_ids.items())),
                node.encoded,
            )
            next_id: _FileIdentity | None = None
            next_digest: str | None = None
            coverage: dict[str, dict[str, int]] | None = None
            if file is not None:
                if (
                    manifest_bytes is not None
                    or any(f.relative_path == file.relative_path for f in files)
                    or journal["coverage"] is not None
                ):
                    raise ValueError("result artifact completed file is duplicated")
                next_id = _parse_identity(journal["new_checkpoint_identity"])
                next_digest = _identifier(journal["new_checkpoint_sha256"])
                if next_id != stage_ids["checkpoint"] or next_digest != _digest(
                    _checkpoint(identity, (*files, file), context)
                ):
                    raise ValueError("result artifact future checkpoint is invalid")
                published = nodes.get(file.relative_path)
                if published is not None:
                    if (
                        published.kind != "file"
                        or published.identity != stage_ids["data"]
                        or _digest(published.encoded) != file.sha256
                        or len(published.encoded) != file.byte_count
                    ):
                        raise ValueError("result artifact completed file changed")
                    expected_names.add(file.relative_path)
                if (
                    is_last
                    and cp.identity == current_id
                    and _digest(cp.encoded) == current_digest
                ):
                    pending = True
                else:
                    if published is None:
                        raise ValueError("result artifact completed file is missing")
                files.append(file)
                current_id, current_digest = next_id, next_digest
            else:
                if (
                    not is_last
                    or journal["new_checkpoint_identity"] is not None
                    or journal["new_checkpoint_sha256"] is not None
                ):
                    raise ValueError("result artifact manifest publication is invalid")
                coverage = _coverage(journal["coverage"])
                manifest_bytes = _manifest(identity, files, coverage, context)
                published_manifest = nodes.get("manifest.json")
                if published_manifest is not None:
                    if (
                        published_manifest.kind != "file"
                        or published_manifest.identity != stage_ids["data"]
                        or published_manifest.encoded != manifest_bytes
                    ):
                        raise ValueError("result artifact manifest changed")
                    expected_names.add("manifest.json")
                else:
                    pending = True
            observed_stage = store.stage_snapshot(tx_token)
            committed = not (is_last and pending)
            if observed_stage is not None:
                if observed_stage.identity != stage.identity:
                    raise ValueError(
                        "result artifact staging directory identity changed"
                    )
                staged_nodes = observed_stage.mapping()
                if not set(staged_nodes) <= required_stage_names:
                    raise ValueError(
                        "result artifact staging inventory contains unknown objects"
                    )
                for key, staged_node in staged_nodes.items():
                    if (
                        staged_node.kind != "file"
                        or staged_node.identity != stage_ids[key]
                    ):
                        raise ValueError("result artifact staging identity changed")
                    expected_bytes = (
                        node.encoded
                        if key == "journal"
                        else _checkpoint(identity, files, context)
                        if key == "checkpoint"
                        else manifest_bytes
                        if file is None
                        else None
                    )
                    if (
                        expected_bytes is not None
                        and staged_node.encoded != expected_bytes
                    ):
                        raise ValueError("result artifact staging bytes changed")
                    if (
                        key == "data"
                        and file is not None
                        and (
                            _digest(staged_node.encoded) != file.sha256
                            or len(staged_node.encoded) != file.byte_count
                        )
                    ):
                        raise ValueError("result artifact staging bytes changed")
                if not committed:
                    if (
                        "data" not in staged_nodes
                        or "journal" not in staged_nodes
                        or (file is not None and "checkpoint" not in staged_nodes)
                    ):
                        raise ValueError(
                            "result artifact pending staging evidence is incomplete"
                        )
            elif not committed:
                raise ValueError("result artifact pending staging evidence is missing")
            if observed_stage is not None:
                stage = replace(stage, expected_nodes=observed_stage.nodes)
            transactions.append(
                _Transaction(
                    name,
                    stage,
                    file,
                    _parse_identity(journal["old_checkpoint_identity"]),
                    next_id,
                    next_digest,
                    node.encoded,
                )
            )
            stages.append((tx_token, observed_stage))
            expected_names.add(name)
        if not pending:
            if (
                cp.identity != current_id
                or _digest(cp.encoded) != current_digest
                or cp.encoded != _checkpoint(identity, files, context)
            ):
                raise ValueError("result artifact checkpoint changed")
        elif transactions[-1].file is not None:
            if cp.encoded != _checkpoint(identity, files[:-1], context):
                raise ValueError("result artifact pending checkpoint changed")
        elif cp.identity != current_id or cp.encoded != _checkpoint(
            identity, files, context
        ):
            raise ValueError("result artifact finalize checkpoint changed")
        if set(nodes) != expected_names:
            raise ValueError("result artifact inventory contains unknown objects")
        return _Observation(
            snapshot,
            tuple(stages),
            tuple(files),
            context,
            token,
            tuple(transactions),
            pending,
            manifest_bytes if "manifest.json" in nodes else None,
        )

    def _revalidate(
        self, store: _Store, identity: ResultArtifactIdentity, observation: _Observation
    ) -> None:
        if self._observe(store, identity) != observation:
            raise ValueError("result artifact state changed before apply")

    def _recover(
        self, store: _Store, identity: ResultArtifactIdentity, observation: _Observation
    ) -> _Observation:
        # All root/stage objects and every cleanup entry are validated together.
        self._phase("before_apply")
        self._revalidate(store, identity, observation)
        if observation.pending:
            tx = observation.transactions[-1]
            nodes = observation.snapshot.mapping()
            destination = (
                tx.file.relative_path if tx.file is not None else "manifest.json"
            )
            if destination not in nodes:
                store.publish_file(tx.stage, destination)
            if tx.file is not None:
                store.replace_checkpoint(tx.stage, nodes["checkpoint.json"])
        # Complete failed durability boundaries even when the namespace is readable.
        store.sync()
        sealed = self._observe(store, identity)
        if sealed.pending:
            raise ValueError("result artifact transaction did not commit")
        self._revalidate(store, identity, sealed)
        for tx, (_, stage_snapshot) in zip(
            sealed.transactions, sealed.stages, strict=True
        ):
            if stage_snapshot is not None:
                store.cleanup(tx.stage, stage_snapshot)
        return self._observe(store, identity)

    def read(
        self,
        path: Path,
        identity: ResultArtifactIdentity,
        expected: _FileIdentity,
        relative_path: str,
    ) -> bytes:
        with self._session(path, expected) as store:
            observation = self._observe(store, identity)
            if observation.pending:
                raise ValueError("result artifact requires explicit resume")
            if type(relative_path) is not str or not any(
                file.relative_path == relative_path for file in observation.files
            ):
                raise ValueError("result artifact file is not completed")
            return observation.snapshot.mapping()[relative_path].encoded

    def transact(
        self,
        path: Path,
        identity: ResultArtifactIdentity,
        expected: _FileIdentity | None,
        action: str,
        file: _File | None = None,
        encoded: bytes | None = None,
        coverage: object = None,
        receipt: object = None,
    ) -> tuple[_FileIdentity, dict[str, object] | None]:
        with self._session(path, expected) as store:
            observation = self._observe(store, identity)
            if action in {"resume", "recover"}:
                observation = (
                    self._recover(store, identity, observation)
                    if observation.pending
                    or any(s is not None for _, s in observation.stages)
                    else observation
                )
                store.sync()
                self._revalidate(store, identity, observation)
                if observation.manifest_bytes is not None:
                    return store.root_identity, _receipt(
                        identity,
                        observation.files,
                        observation.manifest_bytes,
                        observation.context,
                    )
                if action == "recover":
                    raise ValueError("result artifact is not finalized")
                return store.root_identity, None
            if action == "validate":
                if observation.pending or observation.manifest_bytes is None:
                    raise ValueError("result artifact is not finalized")
                expected_receipt = _receipt(
                    identity,
                    observation.files,
                    observation.manifest_bytes,
                    observation.context,
                )
                if (
                    type(receipt) is not dict
                    or type(receipt.get("file_count")) is not int
                    or type(receipt.get("record_count")) is not int
                    or _json(receipt) != _json(expected_receipt)
                    or len(_json(receipt)[:-1]) > _MAX_RECEIPT_BYTES
                ):
                    raise ValueError("result artifact receipt is invalid")
                return store.root_identity, None
            if action == "checkpoint":
                if observation.pending:
                    raise ValueError("result artifact requires explicit resume")
                return store.root_identity, _object(
                    observation.snapshot.mapping()["checkpoint.json"].encoded
                )
            if observation.manifest_bytes is not None:
                raise ValueError("result artifact is finalized and write-once")
            if observation.pending or any(s is not None for _, s in observation.stages):
                raise ValueError("result artifact requires explicit resume")
            next_cp: bytes | None = None
            if action == "write":
                assert file is not None and encoded is not None
                if any(
                    f.relative_path == file.relative_path for f in observation.files
                ):
                    raise ValueError("result artifact completed file is write-once")
                next_cp = _checkpoint(
                    identity, (*observation.files, file), observation.context
                )
                data = encoded
            elif action == "finalize":
                data = _manifest(
                    identity, observation.files, coverage, observation.context
                )
                _receipt(identity, observation.files, data, observation.context)
            else:
                raise ValueError("result artifact transaction is invalid")
            self._phase("before_apply")
            self._revalidate(store, identity, observation)
            token = secrets.token_hex(32)
            sequence = len(observation.transactions) + 1
            old_cp = observation.snapshot.mapping()["checkpoint.json"]

            def journal(stage: _Stage) -> bytes:
                ids = stage.identities()
                return _json(
                    {
                        "type": "publication",
                        "token": token,
                        "creation_token": observation.token,
                        "root_identity": _hex_identity(store.root_identity),
                        "sequence": sequence,
                        "journal_identity": _hex_identity(ids["journal"]),
                        "stage_identity": _hex_identity(stage.identity),
                        "stage_files": {
                            key: _hex_identity(value) for key, value in ids.items()
                        },
                        "old_checkpoint_identity": _hex_identity(old_cp.identity),
                        "old_checkpoint_sha256": _digest(old_cp.encoded),
                        "new_checkpoint_identity": _hex_identity(ids["checkpoint"])
                        if next_cp is not None
                        else None,
                        "new_checkpoint_sha256": _digest(next_cp)
                        if next_cp is not None
                        else None,
                        "file": file.payload() if file is not None else None,
                        "coverage": _coverage(coverage)
                        if action == "finalize"
                        else None,
                    }
                )

            stage = store.prepare(token, data, next_cp, journal)
            # Staging has not published any artifact name. Recheck the full old
            # artifact before its journal becomes visible, then recover from disk.
            self._revalidate(store, identity, observation)
            store.publish_journal(stage, f".protocol/txn-{sequence:06d}-{token}.json")
            published = self._observe(store, identity)
            completed = self._recover(store, identity, published)
            if action == "finalize":
                assert completed.manifest_bytes is not None
                return store.root_identity, _receipt(
                    identity,
                    completed.files,
                    completed.manifest_bytes,
                    completed.context,
                )
            return store.root_identity, None


class ProductionFilesystemAdapter(_TransactionalAdapter):
    """POSIX local-storage Adapter; every raw persistence operation lives here.

    macOS requires the volume's RENAME_EXCL capability. Linux requires a known
    local filesystem, kernel >= 4.9, and renameat2. Other environments reject
    before creating a staging object. Directory rename is exclusive; checkpoint
    replacement is a cooperative-lock operation, never an inode CAS promise.
    """

    class _AttrList(ctypes.Structure):
        _fields_ = [
            ("bitmapcount", ctypes.c_uint16),
            ("reserved", ctypes.c_uint16),
            ("commonattr", ctypes.c_uint32),
            ("volattr", ctypes.c_uint32),
            ("dirattr", ctypes.c_uint32),
            ("fileattr", ctypes.c_uint32),
            ("forkattr", ctypes.c_uint32),
        ]

    class _Capabilities(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_uint32),
            ("capabilities", ctypes.c_uint32 * 4),
            ("valid", ctypes.c_uint32 * 4),
        ]

    class _DarwinStatfs(ctypes.Structure):
        _fields_ = [
            ("f_bsize", ctypes.c_uint32),
            ("f_iosize", ctypes.c_int32),
            ("f_blocks", ctypes.c_uint64),
            ("f_bfree", ctypes.c_uint64),
            ("f_bavail", ctypes.c_uint64),
            ("f_files", ctypes.c_uint64),
            ("f_ffree", ctypes.c_uint64),
            ("f_fsid", ctypes.c_int32 * 2),
            ("f_owner", ctypes.c_uint32),
            ("f_type", ctypes.c_uint32),
            ("f_flags", ctypes.c_uint32),
            ("f_fssubtype", ctypes.c_uint32),
            ("f_fstypename", ctypes.c_char * 16),
            ("f_mntonname", ctypes.c_char * 1024),
            ("f_mntfromname", ctypes.c_char * 1024),
            ("f_flags_ext", ctypes.c_uint32),
            ("f_reserved", ctypes.c_uint32 * 7),
        ]

    def _supported(self, directory: int) -> None:
        if not _USE_ANCHORED_DIRECTORY_FD:
            raise ValueError("result artifact production persistence is unavailable")
        libc = ctypes.CDLL(None, use_errno=True)
        if sys.platform == "darwin":
            try:
                # Darwin inode64 ABI: arm64 uses the ordinary symbol; Intel
                # requires the INODE64 alias for this SDK structure.
                symbol = {"arm64": "fstatfs", "x86_64": "fstatfs$INODE64"}.get(
                    os.uname().machine
                )
                if (
                    symbol is None
                    or ctypes.sizeof(ctypes.c_void_p) != 8
                    or ctypes.sizeof(self._DarwinStatfs) != 2168
                ):
                    raise ValueError("result artifact filesystem ABI is unavailable")
                mount_query = getattr(libc, symbol)
                mount_query.argtypes = [
                    ctypes.c_int,
                    ctypes.POINTER(self._DarwinStatfs),
                ]
                mount_query.restype = ctypes.c_int
                mount = self._DarwinStatfs()
                if (
                    mount_query(directory, ctypes.byref(mount)) != 0
                    or not mount.f_flags & 0x1000
                    or mount.f_flags & 1
                ):
                    raise ValueError(
                        "result artifact filesystem must be local and writable"
                    )
                query = libc.fgetattrlist
                query.argtypes = [
                    ctypes.c_int,
                    ctypes.POINTER(self._AttrList),
                    ctypes.c_void_p,
                    ctypes.c_size_t,
                    ctypes.c_uint,
                ]
                query.restype = ctypes.c_int
                request = self._AttrList(5, 0, 0, 0x80020000, 0, 0, 0)
                reply = self._Capabilities()
                result = query(
                    directory,
                    ctypes.byref(request),
                    ctypes.byref(reply),
                    ctypes.sizeof(reply),
                    0,
                )
                if (
                    result != 0
                    or reply.length != ctypes.sizeof(reply)
                    or not (reply.valid[1] & reply.capabilities[1] & 0x80000)
                ):
                    raise ValueError(
                        "result artifact filesystem capabilities are unavailable"
                    )
                libc.renameatx_np
            except AttributeError:
                raise ValueError(
                    "result artifact production persistence is unavailable"
                ) from None
        elif sys.platform.startswith("linux"):
            try:
                version = tuple(
                    int(part)
                    for part in os.uname().release.split("-")[0].split(".")[:2]
                )
                filesystem = ctypes.create_string_buffer(256)
                probe = libc.fstatfs
                probe.argtypes = [ctypes.c_int, ctypes.c_void_p]
                probe.restype = ctypes.c_int
                if probe(directory, filesystem) != 0 or version < (4, 9):
                    raise ValueError(
                        "result artifact filesystem capabilities are unavailable"
                    )
                magic = ctypes.c_long.from_buffer(filesystem).value & 0xFFFFFFFF
                if magic not in {0xEF53, 0x9123683E, 0x58465342, 0x01021994}:
                    raise ValueError(
                        "result artifact filesystem capabilities are unavailable"
                    )
                if os.fstatvfs(directory).f_flag & os.ST_RDONLY:
                    raise ValueError("result artifact filesystem must be writable")
                libc.renameat2
            except (AttributeError, ValueError):
                raise ValueError(
                    "result artifact production persistence is unavailable"
                ) from None
        else:
            raise ValueError("result artifact production persistence is unavailable")
        self._sync_fd(directory, directory=True)

    def _rename_exclusive(self, parent: int, source: str, destination: str) -> None:
        libc = ctypes.CDLL(None, use_errno=True)
        function = libc.renameatx_np if sys.platform == "darwin" else libc.renameat2
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        function.restype = ctypes.c_int
        result = function(
            parent,
            os.fsencode(source),
            parent,
            os.fsencode(destination),
            4 if sys.platform == "darwin" else 1,
        )
        if result != 0:
            code = ctypes.get_errno()
            raise OSError(code, "exclusive result artifact publication failed")

    @staticmethod
    def _id(metadata: os.stat_result) -> _FileIdentity:
        identity = metadata.st_dev, metadata.st_ino
        _hex_identity(identity)
        return identity

    @contextmanager
    def _parent(self, path: Path) -> Iterator[tuple[int, tuple[_FileIdentity, ...]]]:
        if not _USE_ANCHORED_DIRECTORY_FD:
            raise ValueError("result artifact production persistence is unavailable")
        descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        chain = [self._id(os.fstat(descriptor))]
        try:
            for component in path.parent.parts[1:]:
                following = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=descriptor,
                )
                os.close(descriptor)
                descriptor = following
                chain.append(self._id(os.fstat(descriptor)))
            yield descriptor, tuple(chain)
        finally:
            os.close(descriptor)

    def _sync_fd(self, descriptor: int, *, directory: bool = False) -> None:
        kind = "directory" if directory else "file"
        self._phase(f"before_{kind}_fsync")
        os.fsync(descriptor)
        self._phase(f"after_{kind}_fsync")

    def _write(self, descriptor: int, encoded: bytes) -> None:
        offset = 0
        while offset < len(encoded):
            count = os.write(descriptor, encoded[offset:])
            if count <= 0:
                raise OSError(errno.EIO, "result artifact write made no progress")
            offset += count
        self._sync_fd(descriptor)

    def _read(self, descriptor: int, maximum: int = _MAX_FILE_BYTES) -> bytes:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            raise ValueError("result artifact file is unsafe or oversized")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        encoded = b"".join(chunks)
        after = os.fstat(descriptor)
        if len(encoded) > maximum or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ValueError("result artifact file changed while observing")
        return encoded

    def _scan(self, descriptor: int) -> _Snapshot:
        nodes: list[_Node] = []

        def walk(parent: int, prefix: str) -> None:
            for name in sorted(os.listdir(parent)):
                if not name or name in {".", ".."} or "/" in name:
                    raise ValueError("result artifact inventory is invalid")
                relative = prefix + name
                metadata = os.stat(name, dir_fd=parent, follow_symlinks=False)
                identity = self._id(metadata)
                if stat.S_ISDIR(metadata.st_mode):
                    opened = os.open(
                        name,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=parent,
                    )
                    try:
                        if self._id(os.fstat(opened)) != identity:
                            raise ValueError(
                                "result artifact directory changed while observing"
                            )
                        nodes.append(_Node(relative, identity, "directory"))
                        walk(opened, relative + "/")
                    finally:
                        os.close(opened)
                elif stat.S_ISREG(metadata.st_mode):
                    opened = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
                    try:
                        if self._id(os.fstat(opened)) != identity:
                            raise ValueError(
                                "result artifact file changed while observing"
                            )
                        nodes.append(
                            _Node(relative, identity, "file", self._read(opened))
                        )
                    finally:
                        os.close(opened)
                else:
                    raise ValueError(
                        "result artifact inventory contains unsafe objects"
                    )

        walk(descriptor, "")
        return _Snapshot(
            self._id(os.fstat(descriptor)),
            tuple(sorted(nodes, key=lambda n: n.relative_path)),
        )

    class _Session:
        def __init__(
            self,
            adapter: ProductionFilesystemAdapter,
            path: Path,
            parent: int,
            chain: tuple[_FileIdentity, ...],
            root: int,
        ) -> None:
            self.adapter, self.path, self.parent, self.chain, self.root = (
                adapter,
                path,
                parent,
                chain,
                root,
            )
            self.root_identity = adapter._id(os.fstat(root))
            self.ancestor_identities = chain

        def _anchor(self) -> None:
            with self.adapter._parent(self.path) as (parent, chain):
                if chain != self.chain or self.adapter._id(
                    os.fstat(parent)
                ) != self.adapter._id(os.fstat(self.parent)):
                    raise ValueError("result artifact ancestor identity changed")
                metadata = os.stat(self.path.name, dir_fd=parent, follow_symlinks=False)
                if (
                    not stat.S_ISDIR(metadata.st_mode)
                    or self.adapter._id(metadata) != self.root_identity
                ):
                    raise ValueError("result artifact root identity changed")

        @contextmanager
        def _directory(self, relative: str) -> Iterator[int]:
            descriptor = os.dup(self.root)
            try:
                if relative:
                    for component in relative.split("/"):
                        following = os.open(
                            component,
                            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=descriptor,
                        )
                        os.close(descriptor)
                        descriptor = following
                yield descriptor
            finally:
                os.close(descriptor)

        @contextmanager
        def _stage(self, stage: _Stage) -> Iterator[int]:
            name = f".result-artifact-{stage.token}.stage"
            opened = os.open(
                name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.parent
            )
            try:
                if self.adapter._id(os.fstat(opened)) != stage.identity:
                    raise ValueError(
                        "result artifact staging directory identity changed"
                    )
                if stage.expected_nodes is not None and self.adapter._scan(
                    opened
                ) != _Snapshot(stage.identity, stage.expected_nodes):
                    raise ValueError(
                        "result artifact staging inventory or bytes changed before publication"
                    )
                named = os.stat(name, dir_fd=self.parent, follow_symlinks=False)
                if (
                    not stat.S_ISDIR(named.st_mode)
                    or self.adapter._id(named) != stage.identity
                ):
                    raise ValueError(
                        "result artifact staging name changed before publication"
                    )
                yield opened
            finally:
                os.close(opened)

        def snapshot(self) -> _Snapshot:
            self._anchor()
            value = self.adapter._scan(self.root)
            self._anchor()
            return value

        def stage_snapshot(self, token: str) -> _Snapshot | None:
            self._anchor()
            try:
                descriptor = os.open(
                    f".result-artifact-{token}.stage",
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=self.parent,
                )
            except FileNotFoundError:
                return None
            try:
                return self.adapter._scan(descriptor)
            finally:
                os.close(descriptor)

        def prepare(
            self,
            token: str,
            data: bytes,
            checkpoint: bytes | None,
            journal: Callable[[_Stage], bytes],
        ) -> _Stage:
            self._anchor()
            name = f".result-artifact-{token}.stage"
            os.mkdir(name, 0o700, dir_fd=self.parent)
            created_identity = self.adapter._id(
                os.stat(name, dir_fd=self.parent, follow_symlinks=False)
            )
            self.adapter._phase("after_stage_directory_created")
            descriptor = os.open(
                name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.parent
            )
            opened: dict[str, int] = {}
            try:
                named = os.stat(name, dir_fd=self.parent, follow_symlinks=False)
                if (
                    self.adapter._id(os.fstat(descriptor)) != created_identity
                    or self.adapter._id(named) != created_identity
                    or self.adapter._scan(descriptor).nodes
                ):
                    raise ValueError(
                        "result artifact new staging directory binding changed"
                    )
                for key in (
                    "data",
                    "journal",
                    *(("checkpoint",) if checkpoint is not None else ()),
                ):
                    opened[key] = os.open(
                        key,
                        os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=descriptor,
                    )
                    self.adapter._phase("after_stage_file_created")
                stage = _Stage(
                    token,
                    self.adapter._id(os.fstat(descriptor)),
                    tuple(
                        sorted(
                            (key, self.adapter._id(os.fstat(fd)))
                            for key, fd in opened.items()
                        )
                    ),
                )
                journal_bytes = journal(stage)
                _object(journal_bytes)
                self.adapter._write(opened["data"], data)
                self.adapter._phase("after_stage_data_written")
                if checkpoint is not None:
                    self.adapter._write(opened["checkpoint"], checkpoint)
                    self.adapter._phase("after_stage_checkpoint_written")
                self.adapter._write(opened["journal"], journal_bytes)
                self.adapter._phase("after_stage_journal_written")
                self.adapter._sync_fd(descriptor, directory=True)
                self.adapter._sync_fd(self.parent, directory=True)
                self.adapter._phase("after_stage_prepared")
                ids = stage.identities()
                expected = [
                    _Node("data", ids["data"], "file", data),
                    _Node("journal", ids["journal"], "file", journal_bytes),
                ]
                if checkpoint is not None:
                    expected.append(
                        _Node("checkpoint", ids["checkpoint"], "file", checkpoint)
                    )
                return _Stage(
                    stage.token,
                    stage.identity,
                    stage.files,
                    journal_bytes,
                    tuple(sorted(expected, key=lambda item: item.relative_path)),
                )
            finally:
                for fd in opened.values():
                    os.close(fd)
                os.close(descriptor)

        def publish_journal(self, stage: _Stage, relative_path: str) -> None:
            self._anchor()
            with (
                self._stage(stage) as source,
                self._directory(_PROTOCOL) as destination,
            ):
                os.link(
                    "journal",
                    PurePosixPath(relative_path).name,
                    src_dir_fd=source,
                    dst_dir_fd=destination,
                    follow_symlinks=False,
                )
                self.adapter._phase("after_journal_publication")
                self.adapter._sync_fd(destination, directory=True)

        def publish_file(self, stage: _Stage, relative_path: str) -> None:
            self._anchor()
            relative = PurePosixPath(relative_path)
            with (
                self._stage(stage) as source,
                self._directory(
                    str(relative.parent) if str(relative.parent) != "." else ""
                ) as destination,
            ):
                current = os.stat("data", dir_fd=source, follow_symlinks=False)
                if self.adapter._id(current) != stage.identities()["data"]:
                    raise ValueError("result artifact staged content identity changed")
                os.link(
                    "data",
                    relative.name,
                    src_dir_fd=source,
                    dst_dir_fd=destination,
                    follow_symlinks=False,
                )
                self.adapter._phase(
                    "after_manifest_publication"
                    if relative_path == "manifest.json"
                    else "after_final_publication"
                )
                self.adapter._sync_fd(destination, directory=True)

        def replace_checkpoint(self, stage: _Stage, previous: _Node) -> None:
            self._anchor()
            with self._stage(stage) as source:
                metadata = os.stat(
                    "checkpoint.json", dir_fd=self.root, follow_symlinks=False
                )
                if self.adapter._id(metadata) != previous.identity:
                    raise ValueError("result artifact checkpoint identity changed")
                descriptor = os.open(
                    "checkpoint.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.root
                )
                try:
                    if self.adapter._id(os.fstat(descriptor)) != previous.identity:
                        raise ValueError("result artifact checkpoint identity changed")
                    if self.adapter._read(descriptor) != previous.encoded:
                        raise ValueError("result artifact checkpoint bytes changed")
                finally:
                    os.close(descriptor)
                staged = os.stat("checkpoint", dir_fd=source, follow_symlinks=False)
                if self.adapter._id(staged) != stage.identities()["checkpoint"]:
                    raise ValueError(
                        "result artifact staged checkpoint identity changed"
                    )
                os.rename(
                    "checkpoint",
                    "checkpoint.json",
                    src_dir_fd=source,
                    dst_dir_fd=self.root,
                )
                self.adapter._phase("after_checkpoint_replacement")
                self.adapter._sync_fd(self.root, directory=True)
                self.adapter._sync_fd(source, directory=True)

        def cleanup(self, stage: _Stage, observed: _Snapshot) -> None:
            self._anchor()
            with self._stage(stage) as descriptor:
                remaining = observed.mapping()
                for name in sorted(remaining):
                    self._anchor()
                    actual = self.adapter._scan(descriptor)
                    if (
                        actual.identity != observed.identity
                        or actual.mapping() != remaining
                    ):
                        raise ValueError(
                            "result artifact staging state changed during cleanup"
                        )
                    os.unlink(name, dir_fd=descriptor)
                    del remaining[name]
                    self.adapter._phase("after_stage_cleanup_item")
                    self.adapter._sync_fd(descriptor, directory=True)
                self._anchor()
                if self.adapter._scan(descriptor).nodes:
                    raise ValueError(
                        "result artifact staging state changed during cleanup"
                    )
                metadata = os.stat(
                    f".result-artifact-{stage.token}.stage",
                    dir_fd=self.parent,
                    follow_symlinks=False,
                )
                if self.adapter._id(metadata) != stage.identity:
                    raise ValueError(
                        "result artifact staging directory identity changed"
                    )
                os.rmdir(f".result-artifact-{stage.token}.stage", dir_fd=self.parent)
                self.adapter._phase("after_stage_cleanup")
                self.adapter._sync_fd(self.parent, directory=True)

        def sync(self) -> None:
            self._anchor()
            snapshot = self.adapter._scan(self.root)
            for node in snapshot.nodes:
                if node.kind == "file":
                    relative = PurePosixPath(node.relative_path)
                    with self._directory(
                        str(relative.parent) if str(relative.parent) != "." else ""
                    ) as parent:
                        descriptor = os.open(
                            relative.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent
                        )
                        try:
                            if self.adapter._id(os.fstat(descriptor)) != node.identity:
                                raise ValueError(
                                    "result artifact file identity changed during durability proof"
                                )
                            self.adapter._sync_fd(descriptor)
                        finally:
                            os.close(descriptor)
                else:
                    with self._directory(node.relative_path) as directory:
                        self.adapter._sync_fd(directory, directory=True)
            self.adapter._sync_fd(self.root, directory=True)
            self.adapter._sync_fd(self.parent, directory=True)
            self._anchor()

    @contextmanager
    def _session(self, path: Path, expected: _FileIdentity | None) -> Iterator[_Store]:
        if not _USE_ANCHORED_DIRECTORY_FD or fcntl is None:
            raise ValueError("result artifact production persistence is unavailable")
        with self._parent(path) as (parent, chain):
            root = os.open(
                path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
            )
            try:
                root_id = self._id(os.fstat(root))
                if expected is not None and root_id != expected:
                    raise ValueError("result artifact root identity changed")
                fcntl.flock(root, fcntl.LOCK_EX)
                try:
                    self._supported(parent)
                    session = self._Session(self, path, parent, chain, root)
                    session._anchor()
                    yield session
                finally:
                    fcntl.flock(root, fcntl.LOCK_UN)
            finally:
                os.close(root)

    @contextmanager
    def _created_directory(
        self, root: int, relative: str, known: Mapping[str, _FileIdentity]
    ) -> Iterator[int]:
        descriptor = os.dup(root)
        prefix = ""
        try:
            if relative and relative != ".":
                for component in relative.split("/"):
                    prefix = component if not prefix else prefix + "/" + component
                    following = os.open(
                        component,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=descriptor,
                    )
                    os.close(descriptor)
                    descriptor = following
                    if self._id(os.fstat(descriptor)) != known[prefix]:
                        raise ValueError(
                            "result artifact created directory identity changed"
                        )
            yield descriptor
        finally:
            os.close(descriptor)

    def create(
        self, path: Path, identity: ResultArtifactIdentity, context: bytes | None = None
    ) -> _FileIdentity:
        if fcntl is None:
            raise ValueError("result artifact production persistence is unavailable")
        initial = _checkpoint(identity, context=context)
        with self._parent(path) as (parent, chain):
            self._supported(parent)
            try:
                os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ValueError("result artifact create target already exists")
            self._phase("before_apply")
            with self._parent(path) as (_, refreshed):
                if refreshed != chain:
                    raise ValueError(
                        "result artifact ancestor identity changed before apply"
                    )
            maximum_identity = (_MAX_IDENTITY, _MAX_IDENTITY)
            _creation_ownership(
                identity,
                context,
                "f" * 64,
                maximum_identity,
                maximum_identity,
                {name: maximum_identity for name in _FIXED_DIRECTORIES},
                maximum_identity,
                initial,
                chain,
            )
            # Absence is part of the preflight plan, just like ancestor ids.
            # Recheck before the first private namespace mutation; RENAME_EXCL
            # still handles later races after legal preparation has begun.
            try:
                os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ValueError("result artifact create target changed before apply")
            token = secrets.token_hex(32)
            stage_name = f".result-artifact-create-{token}.stage"
            os.mkdir(stage_name, 0o700, dir_fd=parent)
            created_identity = self._id(
                os.stat(stage_name, dir_fd=parent, follow_symlinks=False)
            )
            self._phase("after_create_stage_directory_created")
            root = os.open(
                stage_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
            )
            locked = False
            try:
                root_id = self._id(os.fstat(root))
                named = os.stat(stage_name, dir_fd=parent, follow_symlinks=False)
                if (
                    root_id != created_identity
                    or self._id(named) != created_identity
                    or self._scan(root).nodes
                ):
                    raise ValueError(
                        "result artifact new prepared root binding changed"
                    )
                # The staged inode becomes the public root without changing its
                # lock domain. Hold it before publication until create completes.
                fcntl.flock(root, fcntl.LOCK_EX)
                locked = True
                directories: dict[str, _FileIdentity] = {}
                for relative in _FIXED_DIRECTORIES:
                    coordinate = PurePosixPath(relative)
                    with self._created_directory(
                        root, str(coordinate.parent), directories
                    ) as directory_parent:
                        os.mkdir(coordinate.name, 0o700, dir_fd=directory_parent)
                        directories[relative] = self._id(
                            os.stat(
                                coordinate.name,
                                dir_fd=directory_parent,
                                follow_symlinks=False,
                            )
                        )
                expected_directories = tuple(
                    _Node(name, directory_id, "directory")
                    for name, directory_id in directories.items()
                )
                directory_plan = _Snapshot(
                    root_id,
                    tuple(
                        sorted(
                            expected_directories, key=lambda node: node.relative_path
                        )
                    ),
                )
                if self._scan(root) != directory_plan:
                    raise ValueError(
                        "result artifact created directory inventory changed"
                    )
                cp = os.open(
                    "checkpoint.json",
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=root,
                )
                try:
                    cp_id = self._id(os.fstat(cp))
                    self._write(cp, initial)
                    checkpoint_plan = _Snapshot(
                        root_id,
                        tuple(
                            sorted(
                                (
                                    *expected_directories,
                                    _Node("checkpoint.json", cp_id, "file", initial),
                                ),
                                key=lambda node: node.relative_path,
                            )
                        ),
                    )
                    self._phase("after_create_checkpoint_written")
                    if self._scan(root) != checkpoint_plan:
                        raise ValueError(
                            "result artifact initial checkpoint plan changed"
                        )
                finally:
                    os.close(cp)
                owner_name = f".protocol/create-{token}.json"
                with self._created_directory(root, _PROTOCOL, directories) as protocol:
                    owner = os.open(
                        PurePosixPath(owner_name).name,
                        os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=protocol,
                    )
                    try:
                        owner_id = self._id(os.fstat(owner))
                        owner_bytes = _creation_ownership(
                            identity,
                            context,
                            token,
                            root_id,
                            owner_id,
                            directories,
                            cp_id,
                            initial,
                            chain,
                        )
                        self._write(owner, owner_bytes)
                        # Expected authority comes from the original plan and the
                        # exclusive descriptors, never from a later scan.
                        prepared_root = _Snapshot(
                            root_id,
                            tuple(
                                sorted(
                                    (
                                        *checkpoint_plan.nodes,
                                        _Node(
                                            owner_name, owner_id, "file", owner_bytes
                                        ),
                                    ),
                                    key=lambda node: node.relative_path,
                                )
                            ),
                        )
                        self._phase("after_create_owner_written")
                        if self._scan(root) != prepared_root:
                            raise ValueError(
                                "result artifact creation ownership plan changed"
                            )
                    finally:
                        os.close(owner)
                for relative in reversed(_FIXED_DIRECTORIES):
                    with self._created_directory(
                        root, relative, directories
                    ) as directory:
                        self._sync_fd(directory, directory=True)
                self._sync_fd(root, directory=True)
                self._sync_fd(parent, directory=True)
                if self._scan(root) != prepared_root:
                    raise ValueError("result artifact prepared creation plan changed")
                self._phase("after_create_prepared")
                with self._parent(path) as (_, refreshed):
                    if refreshed != chain:
                        raise ValueError(
                            "result artifact ancestor identity changed before publication"
                        )
                named_stage = os.stat(stage_name, dir_fd=parent, follow_symlinks=False)
                if (
                    not stat.S_ISDIR(named_stage.st_mode)
                    or self._id(named_stage) != root_id
                    or self._scan(root) != prepared_root
                ):
                    raise ValueError(
                        "result artifact prepared root name, identity or inventory changed"
                    )
                # The complete self-identifying root is prepared and durable before
                # its first public name. RENAME_EXCL never adopts an empty caller dir.
                self._rename_exclusive(parent, stage_name, path.name)
                self._phase("after_create_publication")
                self._sync_fd(parent, directory=True)
                self._sync_fd(root, directory=True)
                with self._parent(path) as (current_parent, current_chain):
                    published = os.stat(
                        path.name, dir_fd=current_parent, follow_symlinks=False
                    )
                    if (
                        current_chain != chain
                        or not stat.S_ISDIR(published.st_mode)
                        or self._id(published) != root_id
                        or self._scan(root) != prepared_root
                    ):
                        raise ValueError(
                            "result artifact published root ownership changed"
                        )
                return root_id
            finally:
                try:
                    if locked:
                        fcntl.flock(root, fcntl.LOCK_UN)
                finally:
                    os.close(root)


@dataclass(slots=True)
class _MemoryRoot:
    identity: _FileIdentity
    nodes: dict[str, _Node]
    lock: threading.RLock


class DeterministicFilesystemAdapter(_TransactionalAdapter):
    """An in-memory Adapter for the same transaction contract and fault phases.

    No production descriptor, capability probe, path lookup or raw filesystem
    operation is required. Controlled fixture methods describe outside changes.
    """

    def __init__(self, base: Path, *, identity_start: int = 1) -> None:
        self.base = _path(base)
        if type(identity_start) is not int or not 0 <= identity_start <= _MAX_IDENTITY:
            raise ValueError("result artifact deterministic identity seed is invalid")
        self._ancestor_identities: tuple[_FileIdentity, ...] = (
            (_MAX_IDENTITY, _MAX_IDENTITY),
        )
        self._roots: dict[str, _MemoryRoot] = {}
        self._stages: dict[tuple[str, str], _Snapshot] = {}
        self._next_identity = identity_start
        self._allocation_lock = threading.RLock()
        self._fault: tuple[str, int, BaseException] | None = None
        self._before_apply: Callable[[], None] | None = None

    def _allocate(self) -> _FileIdentity:
        with self._allocation_lock:
            value = (
                _MAX_IDENTITY if self._next_identity > (1 << 63) else 1,
                self._next_identity,
            )
            self._next_identity += 1
            return value

    def interrupt_next(self, phase: str, occurrence: int = 1) -> None:
        self.fail_next(
            phase,
            KeyboardInterrupt("deterministic result artifact interruption"),
            occurrence,
        )

    def fail_next(
        self, phase: str, exception: BaseException | None = None, occurrence: int = 1
    ) -> None:
        if type(phase) is not str or type(occurrence) is not int or occurrence < 1:
            raise ValueError("result artifact fault configuration is invalid")
        self._fault = (
            phase,
            occurrence,
            exception or OSError("deterministic result artifact I/O failure"),
        )

    def pause_next_before_apply(self, callback: Callable[[], None]) -> None:
        self._before_apply = callback

    def _phase(self, phase: str) -> None:
        if phase == "before_apply" and self._before_apply is not None:
            callback, self._before_apply = self._before_apply, None
            callback()
        if self._fault is not None and self._fault[0] == phase:
            name, remaining, exception = self._fault
            self._fault = (name, remaining - 1, exception) if remaining > 1 else None
            if remaining == 1:
                raise exception

    def _sync(self, *, directory: bool = False) -> None:
        kind = "directory" if directory else "file"
        self._phase(f"before_{kind}_fsync")
        self._phase(f"after_{kind}_fsync")

    def exists(self, path: Path) -> bool:
        return str(path) in self._roots

    def read_bytes(self, path: Path, relative_path: str) -> bytes:
        return self._roots[str(path)].nodes[relative_path].encoded

    def inventory(self, path: Path) -> _Snapshot:
        root = self._roots[str(path)]
        return _Snapshot(
            root.identity,
            tuple(sorted(root.nodes.values(), key=lambda n: n.relative_path)),
        )

    def tamper(
        self,
        path: Path,
        relative_path: str,
        encoded: bytes,
        *,
        replace_identity: bool = False,
    ) -> None:
        root = self._roots[str(path)]
        previous = root.nodes.get(relative_path)
        identity = (
            self._allocate()
            if previous is None or replace_identity
            else previous.identity
        )
        root.nodes[relative_path] = _Node(relative_path, identity, "file", encoded)

    def remove(self, path: Path, relative_path: str) -> None:
        del self._roots[str(path)].nodes[relative_path]

    def create(
        self, path: Path, identity: ResultArtifactIdentity, context: bytes | None = None
    ) -> _FileIdentity:
        initial = _checkpoint(identity, context=context)
        with self._allocation_lock:
            if str(path) in self._roots:
                raise ValueError("result artifact create target already exists")
        self._phase("before_apply")
        with self._allocation_lock:
            if str(path) in self._roots:
                raise ValueError("result artifact create target changed before apply")
        token = secrets.token_hex(32)
        root_id = self._allocate()
        self._phase("after_create_stage_directory_created")
        directories = {relative: self._allocate() for relative in _FIXED_DIRECTORIES}
        cp_id, owner_id = self._allocate(), self._allocate()
        owner_name = f".protocol/create-{token}.json"
        owner = _creation_ownership(
            identity,
            context,
            token,
            root_id,
            owner_id,
            directories,
            cp_id,
            initial,
            self._ancestor_identities,
        )
        nodes = {
            relative: _Node(relative, value, "directory")
            for relative, value in directories.items()
        }
        nodes["checkpoint.json"] = _Node("checkpoint.json", cp_id, "file", initial)
        nodes[owner_name] = _Node(owner_name, owner_id, "file", owner)
        self._sync()
        self._phase("after_create_checkpoint_written")
        self._sync()
        self._phase("after_create_owner_written")
        for _ in directories:
            self._sync(directory=True)
        self._phase("after_create_prepared")
        expected_root = _Snapshot(
            root_id,
            tuple(sorted(nodes.values(), key=lambda node: node.relative_path)),
        )
        root = _MemoryRoot(root_id, nodes, threading.RLock())
        with root.lock:
            # Only the exclusive namespace publication uses the allocator lock;
            # durability/fault/transaction work stays in this artifact's domain.
            with self._allocation_lock:
                if str(path) in self._roots:
                    raise ValueError("result artifact create target already exists")
                self._roots[str(path)] = root
            self._phase("after_create_publication")
            self._sync(directory=True)
            self._sync(directory=True)
            if self.inventory(path) != expected_root:
                raise ValueError("result artifact published creation plan changed")
            return root_id

    class _Session:
        def __init__(
            self, adapter: DeterministicFilesystemAdapter, path: Path, root: _MemoryRoot
        ) -> None:
            self.adapter, self.path, self.root = adapter, path, root
            self.root_identity = root.identity
            self.ancestor_identities = adapter._ancestor_identities

        def _anchor(self) -> None:
            if self.adapter._roots.get(str(self.path)) is not self.root:
                raise ValueError("result artifact root identity changed")

        def snapshot(self) -> _Snapshot:
            self._anchor()
            return self.adapter.inventory(self.path)

        def stage_snapshot(self, token: str) -> _Snapshot | None:
            self._anchor()
            return self.adapter._stages.get((str(self.path), token))

        def prepare(
            self,
            token: str,
            data: bytes,
            checkpoint: bytes | None,
            journal: Callable[[_Stage], bytes],
        ) -> _Stage:
            self._anchor()
            stage_id = self.adapter._allocate()
            self.adapter._stages[(str(self.path), token)] = _Snapshot(stage_id, ())
            self.adapter._phase("after_stage_directory_created")
            names = (
                "data",
                "journal",
                *(("checkpoint",) if checkpoint is not None else ()),
            )
            ids = {}
            unprepared = {}
            for name in names:
                ids[name] = self.adapter._allocate()
                unprepared[name] = _Node(name, ids[name], "file")
                self.adapter._stages[(str(self.path), token)] = _Snapshot(
                    stage_id,
                    tuple(sorted(unprepared.values(), key=lambda n: n.relative_path)),
                )
                self.adapter._phase("after_stage_file_created")
            stage = _Stage(token, stage_id, tuple(sorted(ids.items())))
            journal_bytes = journal(stage)
            _object(journal_bytes)
            nodes = [
                _Node("data", ids["data"], "file", data),
                _Node("journal", ids["journal"], "file", journal_bytes),
            ]
            if checkpoint is not None:
                nodes.append(_Node("checkpoint", ids["checkpoint"], "file", checkpoint))
            values = {node.relative_path: node for node in nodes}
            for name, phase in (
                ("data", "after_stage_data_written"),
                ("checkpoint", "after_stage_checkpoint_written"),
                ("journal", "after_stage_journal_written"),
            ):
                if name in values:
                    unprepared[name] = values[name]
                    self.adapter._stages[(str(self.path), token)] = _Snapshot(
                        stage_id,
                        tuple(
                            sorted(unprepared.values(), key=lambda n: n.relative_path)
                        ),
                    )
                    self.adapter._sync()
                    self.adapter._phase(phase)
            self.adapter._sync(directory=True)
            self.adapter._sync(directory=True)
            self.adapter._phase("after_stage_prepared")
            return _Stage(
                token,
                stage_id,
                stage.files,
                journal_bytes,
                tuple(sorted(nodes, key=lambda item: item.relative_path)),
            )

        def _stage(self, stage: _Stage) -> _Snapshot:
            value = self.stage_snapshot(stage.token)
            if value is None or value.identity != stage.identity:
                raise ValueError("result artifact staging directory identity changed")
            if stage.expected_nodes is not None and value != _Snapshot(
                stage.identity, stage.expected_nodes
            ):
                raise ValueError(
                    "result artifact staging inventory or bytes changed before publication"
                )
            return value

        def publish_journal(self, stage: _Stage, relative_path: str) -> None:
            self._anchor()
            if relative_path in self.root.nodes:
                raise ValueError("result artifact publication target exists")
            node = self._stage(stage).mapping()["journal"]
            self.root.nodes[relative_path] = _Node(
                relative_path, node.identity, "file", node.encoded
            )
            self.adapter._phase("after_journal_publication")
            self.adapter._sync(directory=True)

        def publish_file(self, stage: _Stage, relative_path: str) -> None:
            self._anchor()
            if relative_path in self.root.nodes:
                raise ValueError("result artifact publication target exists")
            node = self._stage(stage).mapping()["data"]
            self.root.nodes[relative_path] = _Node(
                relative_path, node.identity, "file", node.encoded
            )
            self.adapter._phase(
                "after_manifest_publication"
                if relative_path == "manifest.json"
                else "after_final_publication"
            )
            self.adapter._sync(directory=True)

        def replace_checkpoint(self, stage: _Stage, previous: _Node) -> None:
            self._anchor()
            if self.root.nodes["checkpoint.json"] != previous:
                raise ValueError("result artifact checkpoint identity changed")
            snapshot = self._stage(stage)
            nodes = snapshot.mapping()
            node = nodes.pop("checkpoint")
            self.root.nodes["checkpoint.json"] = _Node(
                "checkpoint.json", node.identity, "file", node.encoded
            )
            self.adapter._stages[(str(self.path), stage.token)] = _Snapshot(
                snapshot.identity,
                tuple(sorted(nodes.values(), key=lambda n: n.relative_path)),
            )
            self.adapter._phase("after_checkpoint_replacement")
            self.adapter._sync(directory=True)
            self.adapter._sync(directory=True)

        def cleanup(self, stage: _Stage, observed: _Snapshot) -> None:
            self._anchor()
            if self._stage(stage) != observed:
                raise ValueError("result artifact staging state changed before cleanup")
            remaining = observed.mapping()
            for name in sorted(remaining):
                current = self.stage_snapshot(stage.token)
                if (
                    current is None
                    or current.identity != observed.identity
                    or current.mapping() != remaining
                ):
                    raise ValueError(
                        "result artifact staging state changed during cleanup"
                    )
                del remaining[name]
                self.adapter._stages[(str(self.path), stage.token)] = _Snapshot(
                    observed.identity,
                    tuple(sorted(remaining.values(), key=lambda n: n.relative_path)),
                )
                self.adapter._phase("after_stage_cleanup_item")
                self.adapter._sync(directory=True)
            del self.adapter._stages[(str(self.path), stage.token)]
            self.adapter._phase("after_stage_cleanup")
            self.adapter._sync(directory=True)

        def sync(self) -> None:
            self._anchor()
            for node in self.root.nodes.values():
                self.adapter._sync(directory=node.kind == "directory")
            self.adapter._sync(directory=True)
            self.adapter._sync(directory=True)

    @contextmanager
    def _session(self, path: Path, expected: _FileIdentity | None) -> Iterator[_Store]:
        root = self._roots.get(str(path))
        if root is None or expected is not None and root.identity != expected:
            raise ValueError("result artifact root identity changed")
        with root.lock:
            yield self._Session(self, path, root)


_PRODUCTION_FILESYSTEM = ProductionFilesystemAdapter()


def _adapter(filesystem: _TransactionalAdapter | None) -> _TransactionalAdapter:
    if filesystem is None:
        return _PRODUCTION_FILESYSTEM
    if not isinstance(filesystem, _TransactionalAdapter):
        raise ValueError("result artifact filesystem Adapter is invalid")
    return filesystem


class ResultArtifact:
    """Small internal Interface for complete shards, candidates and receipts.

    resume returns a partial handle or the original finalized receipt. Consumers
    make this one protected call before any source invocation; finalized recovery
    never returns a writable handle or reruns research.
    """

    def __init__(
        self,
        path: Path,
        identity: ResultArtifactIdentity,
        root_identity: _FileIdentity,
        filesystem: _TransactionalAdapter,
    ) -> None:
        self._path, self._identity, self._root_identity, self._filesystem = (
            path,
            identity,
            root_identity,
            filesystem,
        )

    @classmethod
    def create(
        cls,
        path: Path,
        identity: ResultArtifactIdentity,
        *,
        filesystem: _TransactionalAdapter | None = None,
        initial_context: PreparedResultArtifactFile | None = None,
    ) -> ResultArtifact:
        _path(path)
        if not isinstance(identity, ResultArtifactIdentity):
            raise ValueError("result artifact identity is invalid")
        adapter = _adapter(filesystem)
        try:
            root_id = adapter.create(path, identity, _context_input(initial_context))
        except OSError:
            raise ValueError("result artifact creation or durability failed") from None
        return cls(path, identity, root_id, adapter)

    @classmethod
    def resume(
        cls,
        path: Path,
        identity: ResultArtifactIdentity,
        *,
        filesystem: _TransactionalAdapter | None = None,
    ) -> ResultArtifact | dict[str, object]:
        _path(path)
        if not isinstance(identity, ResultArtifactIdentity):
            raise ValueError("result artifact identity is invalid")
        adapter = _adapter(filesystem)
        try:
            root_id, receipt = adapter.transact(path, identity, None, "resume")
        except OSError:
            raise ValueError("result artifact resume or durability failed") from None
        return receipt if receipt is not None else cls(path, identity, root_id, adapter)

    @classmethod
    def recover_finalized_receipt(
        cls,
        path: Path,
        identity: ResultArtifactIdentity,
        *,
        filesystem: _TransactionalAdapter | None = None,
    ) -> dict[str, object]:
        _path(path)
        if not isinstance(identity, ResultArtifactIdentity):
            raise ValueError("result artifact identity is invalid")
        try:
            _, receipt = _adapter(filesystem).transact(path, identity, None, "recover")
        except OSError:
            raise ValueError(
                "result artifact receipt recovery or durability failed"
            ) from None
        assert receipt is not None
        return receipt

    @classmethod
    def validate_receipt(
        cls,
        path: Path,
        identity: ResultArtifactIdentity,
        receipt: object,
        *,
        filesystem: _TransactionalAdapter | None = None,
    ) -> None:
        _path(path)
        if not isinstance(identity, ResultArtifactIdentity):
            raise ValueError("result artifact identity is invalid")
        try:
            _adapter(filesystem).transact(
                path, identity, None, "validate", receipt=receipt
            )
        except OSError:
            raise ValueError("result artifact receipt validation failed") from None

    @staticmethod
    def checkpoint_capacity(
        identity: ResultArtifactIdentity,
        *,
        partition_count: int,
        candidate_count: int = 20,
        initial_context: PreparedResultArtifactFile | None = None,
    ) -> dict[str, int]:
        """Preflight a complete file-count envelope before create (#68/#69).

        Every file uses the longest supported coordinate/schema/count fields.
        The immutable journal owns pending/cleanup and fixed-width 64-bit inode
        fields outside checkpoint.json; no hidden pending checkpoint is larger
        than this envelope. This is an internal proof, not a request option.
        """
        if (
            type(partition_count) is not int
            or not 0 <= partition_count <= 100_000
            or type(candidate_count) is not int
            or not 0 <= candidate_count <= 20
        ):
            raise ValueError("result artifact file-count envelope is invalid")
        partition = _File(
            "partitions/universe/SZSE/part-99999.jsonl",
            "v" * 128,
            "f" * 64,
            _MAX_FILE_BYTES,
            100_000,
        )
        candidate = _File(
            "candidates/SZSE-999999.json", "v" * 128, "f" * 64, _MAX_FILE_BYTES, 1
        )
        overhead = len(_checkpoint(identity, context=_context_input(initial_context)))
        partition_bytes = len(_json(partition.payload())[:-1]) + 1
        candidate_bytes = len(_json(candidate.payload())[:-1]) + 1
        count = partition_count + candidate_count
        worst = (
            overhead
            + partition_bytes * partition_count
            + candidate_bytes * candidate_count
            - (1 if count else 0)
        )
        if worst > _MAX_CHECKPOINT_BYTES:
            raise ValueError(
                "result artifact file-count envelope exceeds checkpoint capacity"
            )
        return {
            "fixed_bytes": overhead,
            "maximum_partition_record_bytes": partition_bytes,
            "maximum_candidate_record_bytes": candidate_bytes,
            "file_count": count,
            "identity_component_bits": 64,
            "identity_component_bytes": 16,
            "pending_checkpoint_bytes": 0,
            "cleanup_checkpoint_bytes": 0,
            "maximum_checkpoint_bytes": worst,
            "checkpoint_limit_bytes": _MAX_CHECKPOINT_BYTES,
        }

    def _transact(
        self,
        action: str,
        *,
        file: _File | None = None,
        encoded: bytes | None = None,
        coverage: object = None,
    ) -> dict[str, object] | None:
        try:
            _, receipt = self._filesystem.transact(
                self._path,
                self._identity,
                self._root_identity,
                action,
                file=file,
                encoded=encoded,
                coverage=coverage,
            )
        except OSError:
            raise ValueError(
                "result artifact transaction or durability failed"
            ) from None
        return receipt

    def checkpoint(self) -> dict[str, object]:
        """Return a detached, validated checkpoint without rewriting it."""
        payload = self._transact("checkpoint")
        assert payload is not None
        return payload

    def write_completed_partition(
        self, prepared_file: PreparedResultArtifactFile
    ) -> None:
        file, encoded = _consume(prepared_file, "partition")
        self._transact("write", file=file, encoded=encoded)

    def write_candidate(self, prepared_file: PreparedResultArtifactFile) -> None:
        file, encoded = _consume(prepared_file, "candidate")
        self._transact("write", file=file, encoded=encoded)

    def read_completed_file(self, relative_path: str) -> bytes:
        """Read verified completed bytes for resume without repeating research."""
        try:
            return self._filesystem.read(
                self._path, self._identity, self._root_identity, relative_path
            )
        except OSError:
            raise ValueError("result artifact completed file read failed") from None

    def finalize(self, *, coverage: object) -> dict[str, object]:
        receipt = self._transact("finalize", coverage=_coverage(coverage))
        assert receipt is not None
        return receipt
