"""Prepare immutable, filesystem-free inputs for Result Artifact persistence."""

from __future__ import annotations

import json
import math
import re
from abc import ABC, ABCMeta, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from enum import Enum, auto
from hashlib import sha256
from ipaddress import IPv6Address, ip_address
from typing import NamedTuple, cast
from unicodedata import normalize
from urllib.parse import parse_qsl, unquote, urlsplit
from weakref import ReferenceType, ref

from .bounded_public_data import (
    MAX_PUBLIC_DATA_DEPTH,
    MAX_PUBLIC_DATA_ITEMS,
    MAX_PUBLIC_DATA_KEY_LENGTH,
    MAX_PUBLIC_DATA_STRING_LENGTH,
)

_MAX_FILE_BYTES = 64 * 1024 * 1024
_MAX_PARTITION_RECORDS = 100_000
_MAX_SHARD_COUNT = 100_000
_MAX_SCHEMA_TOKEN_BYTES = 128
_SCHEMA_TOKEN_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._@+-"
)
_CANONICAL_JSON_ENCODER = json.JSONEncoder(
    allow_nan=False,
    ensure_ascii=False,
    separators=(",", ":"),
    sort_keys=True,
)
_SCHEME_PATTERN = re.compile(r"(?i:https?://)")
_FILE_URI_PATTERN = re.compile(r"(?i:(?<![a-z0-9+.-])file:)")
_UNC_PATH_PATTERN = re.compile(r"\\{2,}[^\\/\s]+\\[^\\/\s]+")
_WINDOWS_DRIVE_PATH_PATTERN = re.compile(r"(?:^|[^A-Za-z0-9/\\])[A-Za-z]:[/\\]")
_AUTHORITY_TERMINATORS = frozenset("/?#")
_INVALID_LOCATOR_CHARACTERS = frozenset('<>"[]|\\^`{}')
_LOCAL_PATH_SUFFIX_DELIMITERS = frozenset("!$'()*+,-.:;@_~")
_PUBLIC_ROOTED_ROUTE_PREFIXES = ("/docs",)
_NON_PUBLIC_DNS_SUFFIXES = frozenset(
    {
        "home",
        "internal",
        "invalid",
        "lan",
        "local",
        "localdomain",
        "localhost",
    }
)
_NON_PUBLIC_DNS_NAMES = ("home.arpa",)
_IANA_NON_GLOBAL_IP_LITERALS = frozenset({"192.88.99.2"})


class _SemanticKeyClass(Enum):
    ORDINARY_PUBLIC = auto()
    SAFE_WHOLE_LEXEME = auto()
    SENSITIVE = auto()


class _SemanticAtomRun(NamedTuple):
    start: int
    end: int
    atoms: frozenset[str]
    atom_count: int


_SAFE_WHOLE_LEXEMES = frozenset(
    {
        "application",
        "applicationkeyrotationstatus",
        "header",
        "key",
        "observerinfo",
        "observerinformation",
        "observerinformationstatus",
        "observerinfostatus",
        "rotation",
        "server",
        "tokenisedwordcount",
        "tokenizedwordcount",
        "wordtoken",
        "wordtokencount",
        "wordtokens",
    }
)
_ORDINARY_WHOLE_LEXEMES = frozenset(
    {
        "capitalkey",
        "tokenisationmethod",
        "tokeniserversion",
        "tokenizationmethod",
        "tokenizerversion",
    }
)
_SENSITIVE_WHOLE_LEXEMES = frozenset(
    {
        "baseurl",
        "callable",
        "endpoint",
        "headers",
        "host",
        "hostname",
        "resultartifact",
        "transport",
    }
)
_SEMANTIC_PLURAL_REPLACEMENTS = (
    ("authorizations", "authorization"),
    ("informations", "information"),
    ("credentials", "credential"),
    ("endpoints", "endpoint"),
    ("addresses", "address"),
    ("hostnames", "hostname"),
    ("passwords", "password"),
    ("payloads", "payload"),
    ("requests", "request"),
    ("servers", "server"),
    ("headers", "header"),
    ("cookies", "cookie"),
    ("details", "detail"),
    ("secrets", "secret"),
    ("tokens", "token"),
    ("hosts", "host"),
    ("keys", "key"),
    ("names", "name"),
    ("ports", "port"),
    ("urls", "url"),
    ("apis", "api"),
    ("ips", "ip"),
    ("maps", "map"),
)
_SENSITIVE_COMPOUND_ATOMS = (
    (frozenset({"api"}), frozenset({"key"})),
    (frozenset({"raw"}), frozenset({"payload"})),
    (frozenset({"request"}), frozenset({"header"})),
    (
        frozenset({"server"}),
        frozenset(
            {
                "address",
                "detail",
                "endpoint",
                "host",
                "hostname",
                "info",
                "information",
                "ip",
                "metadata",
                "name",
                "port",
                "url",
            }
        ),
    ),
    (frozenset({"base"}), frozenset({"url"})),
    (frozenset({"result"}), frozenset({"artifact"})),
)
_SENSITIVE_SINGLE_ATOMS = frozenset(
    {
        "authorization",
        "baseurl",
        "callable",
        "cookie",
        "credential",
        "endpoint",
        "host",
        "hostname",
        "password",
        "resultartifact",
        "secret",
        "token",
        "transport",
    }
)
_QUALIFIABLE_WHOLE_ATOMS = frozenset(
    {
        "baseurl",
        "callable",
        "endpoint",
        "host",
        "hostname",
        "resultartifact",
        "transport",
    }
)
_SAFE_COMPOUND_WHOLE_LEXEMES = _SAFE_WHOLE_LEXEMES - {
    "application",
    "header",
    "key",
    "rotation",
    "server",
}
_ORDINARY_SEMANTIC_HOMOGRAPHS = tuple(
    sorted(
        {
            "apiaries",
            "apiary",
            "capillaries",
            "capillary",
            "capital",
            "capitol",
            "drawer",
            "ghost",
            "ghosts",
            "headerline",
            "hockey",
            "hostess",
            "hostesses",
            "hostage",
            "hostile",
            "hostility",
            "keyboard",
            "keynote",
            "monkey",
            "monkeys",
            "observer",
            "rapid",
            "requester",
            "secretarial",
            "secretariat",
            "secretariats",
            "secretaries",
            "secretary",
            "secretion",
            "secretive",
            "secretory",
            "shaping",
            "strawberry",
            "swordtokenizer",
            "therapist",
            "tokenisation",
            "tokeniser",
            "tokenization",
            "tokenizer",
            "tokenomics",
            "transportation",
            "transportations",
            "wordtokenizer",
        },
        key=lambda value: (-len(value), value),
    )
)
_ORDINARY_PARTIAL_ATOM_TOKENS = frozenset(_ORDINARY_SEMANTIC_HOMOGRAPHS)
_ORDINARY_SAFE_COMPOUND_SUFFIXES = {"wordtoken": ("iser", "izer")}
_SEMANTIC_ATOMS = tuple(
    sorted(
        {
            "access",
            "account",
            "address",
            "annual",
            "api",
            "application",
            "archive",
            "artifact",
            "audit",
            "authorization",
            "backup",
            "base",
            "baseurl",
            "callable",
            "client",
            "cookie",
            "count",
            "credential",
            "detail",
            "digest",
            "endpoint",
            "envelope",
            "for",
            "header",
            "hash",
            "host",
            "hostname",
            "http",
            "info",
            "information",
            "ip",
            "job",
            "key",
            "metadata",
            "name",
            "normalized",
            "party",
            "password",
            "payload",
            "port",
            "primary",
            "provider",
            "proxy",
            "raw",
            "request",
            "response",
            "result",
            "resultartifact",
            "rotation",
            "runtime",
            "secret",
            "server",
            "status",
            "third",
            "tls",
            "token",
            "trace",
            "transport",
            "url",
            "v2",
            "word",
            "working",
            "x",
        }
        | _SAFE_COMPOUND_WHOLE_LEXEMES,
        key=lambda atom: (-len(atom), atom),
    )
)
_SEMANTIC_ATOM_ALIASES = tuple(
    sorted(
        {(atom, atom) for atom in _SEMANTIC_ATOMS}
        | {
            (plural, singular)
            for plural, singular in _SEMANTIC_PLURAL_REPLACEMENTS
            if singular in _SEMANTIC_ATOMS
        },
        key=lambda alias: (-len(alias[0]), alias[0]),
    )
)
_SEMANTIC_ATOM_ALIASES_BY_INITIAL = {
    initial: tuple(
        alias for alias in _SEMANTIC_ATOM_ALIASES if alias[0].startswith(initial)
    )
    for initial in {alias[0][0] for alias in _SEMANTIC_ATOM_ALIASES}
}


class _PreparedValues(NamedTuple):
    relative_path: str
    schema_version: str
    content_bytes: bytes
    sha256: str
    byte_count: int
    record_count: int


_PREPARED_VALUE_INDEX = {
    field_name: index for index, field_name in enumerate(_PreparedValues._fields)
}
_SEALED_PREPARED_TYPES: set[type[object]] = set()


def _build_prepared_registry() -> tuple[
    Callable[[PreparedResultArtifactFile], _PreparedValues],
    Callable[[object], bool],
    Callable[[object, _PreparedValues], None],
]:
    registered_values: dict[
        int,
        tuple[ReferenceType[object], _PreparedValues],
    ] = {}

    def lookup(value: PreparedResultArtifactFile) -> _PreparedValues:
        registered = registered_values.get(id(value))
        if registered is None or registered[0]() is not value:
            raise TypeError("prepared result artifact file is not Module-owned")
        return registered[1]

    def is_registered(instance: object) -> bool:
        registered = registered_values.get(id(instance))
        return registered is not None and registered[0]() is instance

    def register(prepared: object, values: _PreparedValues) -> None:
        identifier = id(prepared)
        if identifier in registered_values or not _prepared_values_are_valid(values):
            raise ValueError("result artifact prepared file is invalid")

        def discard(dead_reference: ReferenceType[object]) -> None:
            registered = registered_values.get(identifier)
            if registered is not None and registered[0] is dead_reference:
                del registered_values[identifier]

        reference = ref(prepared, discard)
        registered_values[identifier] = (reference, values)

    return lookup, is_registered, register


_prepared_values_lookup, _prepared_instance_check, _register_prepared = (
    _build_prepared_registry()
)


class _PreparedResultArtifactFileMeta(ABCMeta):
    def __setattr__(
        cls,
        name: str,
        value: object,
        _sealed_types: set[type[object]] = _SEALED_PREPARED_TYPES,
    ) -> None:
        if cls in _sealed_types:
            raise TypeError("prepared result artifact types are immutable")
        super().__setattr__(name, value)

    def __delattr__(
        cls,
        name: str,
        _sealed_types: set[type[object]] = _SEALED_PREPARED_TYPES,
    ) -> None:
        if cls in _sealed_types:
            raise TypeError("prepared result artifact types are immutable")
        super().__delattr__(name)

    def __instancecheck__(
        cls,
        instance: object,
        _is_registered: Callable[[object], bool] = _prepared_instance_check,
    ) -> bool:
        if type(instance) is not _PreparedResultArtifactFile:
            return False
        return _is_registered(instance)


class PreparedResultArtifactFile(ABC, metaclass=_PreparedResultArtifactFileMeta):
    """Read-only Interface to one Module-owned prepared file value."""

    __slots__ = ()

    def __init_subclass__(
        cls,
        **kwargs: object,
    ) -> None:
        existing_subclasses = (
            subclass
            for subclass in PreparedResultArtifactFile.__subclasses__()
            if subclass is not cls
        )
        if next(existing_subclasses, None) is not None or kwargs:
            raise TypeError("prepared result artifact files cannot be subclassed")
        super().__init_subclass__()

    @property
    @abstractmethod
    def relative_path(self) -> str:
        """Return the validated canonical artifact-relative coordinate."""

    @property
    @abstractmethod
    def schema_version(self) -> str:
        """Return the exact validated schema token."""

    @property
    @abstractmethod
    def content_bytes(self) -> bytes:
        """Return detached canonical UTF-8 file bytes."""

    @property
    @abstractmethod
    def sha256(self) -> str:
        """Return the lowercase SHA-256 digest of content_bytes."""

    @property
    @abstractmethod
    def byte_count(self) -> int:
        """Return the exact content byte count."""

    @property
    @abstractmethod
    def record_count(self) -> int:
        """Return the bounded number of encoded records."""

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PreparedResultArtifactFile):
            return NotImplemented
        return _prepared_values(self) == _prepared_values(other)

    def __hash__(self) -> int:
        return hash(_prepared_values(self))


class _PreparedResultArtifactFile(PreparedResultArtifactFile):
    __slots__ = ("__weakref__",)

    def __new__(cls) -> _PreparedResultArtifactFile:
        raise TypeError("prepared result artifact files are Module-owned")

    def __getattribute__(self, name: str) -> object:
        field_index = _PREPARED_VALUE_INDEX.get(name)
        if field_index is not None:
            return _prepared_values(self)[field_index]
        return object.__getattribute__(self, name)

    @property
    def relative_path(self) -> str:
        return _prepared_values(self).relative_path

    @property
    def schema_version(self) -> str:
        return _prepared_values(self).schema_version

    @property
    def content_bytes(self) -> bytes:
        return _prepared_values(self).content_bytes

    @property
    def sha256(self) -> str:
        return _prepared_values(self).sha256

    @property
    def byte_count(self) -> int:
        return _prepared_values(self).byte_count

    @property
    def record_count(self) -> int:
        return _prepared_values(self).record_count


_SEALED_PREPARED_TYPES.update({PreparedResultArtifactFile, _PreparedResultArtifactFile})


def _prepared_values(
    value: PreparedResultArtifactFile,
    _lookup: Callable[[PreparedResultArtifactFile], _PreparedValues] = (
        _prepared_values_lookup
    ),
) -> _PreparedValues:
    return _lookup(value)


def _prepared_file(
    *,
    relative_path: str,
    schema_version: str,
    content_bytes: bytes,
    record_count: int,
    _register: Callable[[object, _PreparedValues], None] = _register_prepared,
) -> PreparedResultArtifactFile:
    candidate_path = re.fullmatch(
        r"candidates/(?:BSE|SSE|SZSE)-[0-9]{6}\.json",
        relative_path,
    )
    partition_path = re.fullmatch(
        r"partitions/(?:factors|ranking|universe)/(?:BSE|SSE|SZSE)/"
        r"part-[0-9]{5}\.jsonl",
        relative_path,
    )
    if (
        not _valid_schema_token(schema_version)
        or type(content_bytes) is not bytes
        or len(content_bytes) > _MAX_FILE_BYTES
        or type(record_count) is not int
        or not 0 <= record_count <= _MAX_PARTITION_RECORDS
        or (candidate_path is None and partition_path is None)
        or (candidate_path is not None and record_count != 1)
    ):
        raise ValueError("result artifact prepared file is invalid")
    values = _PreparedValues(
        relative_path=relative_path,
        schema_version=schema_version,
        content_bytes=content_bytes,
        sha256=sha256(content_bytes).hexdigest(),
        byte_count=len(content_bytes),
        record_count=record_count,
    )
    prepared = object.__new__(_PreparedResultArtifactFile)
    _register(prepared, values)
    return prepared


def _valid_schema_token(value: object) -> bool:
    return (
        type(value) is str
        and value.isascii()
        and 0 < len(value.encode("utf-8")) <= _MAX_SCHEMA_TOKEN_BYTES
        and value[0].isalnum()
        and all(character in _SCHEMA_TOKEN_CHARACTERS for character in value)
        and ".." not in value
    )


def _prepared_values_are_valid(values: _PreparedValues) -> bool:
    if (
        type(values.relative_path) is not str
        or type(values.schema_version) is not str
        or type(values.content_bytes) is not bytes
        or type(values.sha256) is not str
        or type(values.byte_count) is not int
        or type(values.record_count) is not int
        or not _valid_schema_token(values.schema_version)
        or len(values.content_bytes) > _MAX_FILE_BYTES
        or values.byte_count != len(values.content_bytes)
        or values.sha256 != sha256(values.content_bytes).hexdigest()
    ):
        return False
    candidate_path = re.fullmatch(
        r"candidates/(?:BSE|SSE|SZSE)-[0-9]{6}\.json",
        values.relative_path,
    )
    partition_path = re.fullmatch(
        r"partitions/(?:factors|ranking|universe)/(?:BSE|SSE|SZSE)/"
        r"part-[0-9]{5}\.jsonl",
        values.relative_path,
    )
    if (
        (candidate_path is None and partition_path is None)
        or not 0 <= values.record_count <= _MAX_PARTITION_RECORDS
        or (candidate_path is not None and values.record_count != 1)
    ):
        return False
    lines = values.content_bytes.splitlines(keepends=True)
    if len(lines) != values.record_count or any(
        not line.endswith(b"\n") for line in lines
    ):
        return False
    try:
        parsed_records = [json.loads(line) for line in lines]
        if candidate_path is not None:
            detached_record = _detached_json_value(parsed_records[0])
            detached: object = [detached_record]
        else:
            detached = _detached_json_value(parsed_records)
            if type(detached) is not list:
                return False
        canonical = bytearray()
        for record in cast(list[object], detached):
            if type(record) is not dict:
                return False
            _validate_detached_json(record)
            _append_canonical_json_line(canonical, record)
    except Exception:
        return False
    return bytes(canonical) == values.content_bytes


def _detached_json_value(value: object) -> object:
    memo: dict[int, tuple[object, object, int, int]] = {}
    active: set[int] = set()
    item_count = 0

    def snapshot(item: object, depth: int) -> tuple[object, int, int]:
        nonlocal item_count
        item_count += 1
        if item_count > MAX_PUBLIC_DATA_ITEMS or depth > MAX_PUBLIC_DATA_DEPTH:
            raise ValueError("result artifact persisted document is invalid")

        item_type = type(item)
        if item is None or item_type in {bool, int}:
            return item, 1, 0
        if item_type is str:
            string_item = cast(str, item)
            if len(string_item) > MAX_PUBLIC_DATA_STRING_LENGTH:
                raise ValueError("result artifact persisted document is invalid")
            return string_item, 1, 0
        if item_type is float:
            float_item = cast(float, item)
            if not math.isfinite(float_item):
                raise ValueError("result artifact persisted document is invalid")
            return float_item, 1, 0
        if item_type is Decimal:
            decimal_item = cast(Decimal, item)
            if (
                not decimal_item.is_finite()
                or len(decimal_item.as_tuple().digits) > MAX_PUBLIC_DATA_STRING_LENGTH
            ):
                raise ValueError("result artifact persisted document is invalid")
            decimal_string = str(decimal_item)
            if len(decimal_string) > MAX_PUBLIC_DATA_STRING_LENGTH:
                raise ValueError("result artifact persisted document is invalid")
            return decimal_string, 1, 0

        identity = id(item)
        if identity in active:
            raise ValueError("result artifact persisted document is invalid")
        cached = memo.get(identity)
        if cached is not None and cached[0] is item:
            _, owned, subtree_items, max_relative_depth = cached
            item_count += subtree_items - 1
            if (
                item_count > MAX_PUBLIC_DATA_ITEMS
                or depth + max_relative_depth > MAX_PUBLIC_DATA_DEPTH
            ):
                raise ValueError("result artifact persisted document is invalid")
            return owned, subtree_items, max_relative_depth

        observed_type = item.__class__
        if not isinstance(observed_type, type):
            raise ValueError("result artifact persisted document is invalid")
        if issubclass(observed_type, (str, bytes, bytearray)):
            raise ValueError("result artifact persisted document is invalid")
        is_mapping = issubclass(observed_type, Mapping)
        is_sequence = issubclass(observed_type, Sequence)
        if not is_mapping and not is_sequence:
            raise ValueError("result artifact persisted document is invalid")

        active.add(identity)
        subtree_items = 1
        max_relative_depth = 0
        try:
            if is_mapping:
                detached_mapping: dict[str, object] = {}
                for key, nested in cast(Mapping[object, object], item).items():
                    if (
                        type(key) is not str
                        or len(key) > MAX_PUBLIC_DATA_KEY_LENGTH
                        or key in detached_mapping
                    ):
                        raise ValueError(
                            "result artifact persisted document is invalid"
                        )
                    owned, child_items, child_depth = snapshot(nested, depth + 1)
                    detached_mapping[key] = owned
                    subtree_items += child_items
                    max_relative_depth = max(
                        max_relative_depth,
                        1 + child_depth,
                    )
                detached: object = detached_mapping
            else:
                detached_list: list[object] = []
                for nested in cast(Sequence[object], item):
                    owned, child_items, child_depth = snapshot(nested, depth + 1)
                    detached_list.append(owned)
                    subtree_items += child_items
                    max_relative_depth = max(
                        max_relative_depth,
                        1 + child_depth,
                    )
                detached = detached_list
        finally:
            active.discard(identity)
        memo[identity] = (
            item,
            detached,
            subtree_items,
            max_relative_depth,
        )
        return detached, subtree_items, max_relative_depth

    return snapshot(value, 0)[0]


def _semantic_key_tokens(value: str) -> tuple[str, ...]:
    acronym_separated = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", value)
    camel_separated = re.sub(
        r"(?<=[a-z0-9])(?=[A-Z])",
        " ",
        acronym_separated,
    )
    return tuple(re.findall(r"[a-z0-9]+", camel_separated.casefold()))


def _normalize_semantic_plurals(value: str) -> str:
    normalized = value
    for plural, singular in _SEMANTIC_PLURAL_REPLACEMENTS:
        normalized = normalized.replace(plural, singular)
    return normalized


def _semantic_atom_runs(token: str) -> tuple[_SemanticAtomRun, ...]:
    memo: dict[int, tuple[int, frozenset[str], int]] = {}

    def longest_run(start: int) -> tuple[int, frozenset[str], int]:
        cached = memo.get(start)
        if cached is not None:
            return cached
        best: tuple[int, frozenset[str], int] = (start, frozenset(), 0)
        aliases = (
            _SEMANTIC_ATOM_ALIASES_BY_INITIAL.get(token[start], ())
            if start < len(token)
            else ()
        )
        for lexeme, atom in aliases:
            if not token.startswith(lexeme, start):
                continue
            end, remainder, remainder_count = longest_run(start + len(lexeme))
            if end > best[0]:
                best = (end, remainder | {atom}, remainder_count + 1)
        memo[start] = best
        return best

    runs: list[_SemanticAtomRun] = []
    for start in range(len(token)):
        cursor, atoms, atom_count = longest_run(start)
        if atoms:
            runs.append(_SemanticAtomRun(start, cursor, atoms, atom_count))
    return tuple(runs)


def _semantic_atoms_are_sensitive(
    atoms: frozenset[str],
    *,
    atom_count: int,
    complete_token: bool,
) -> bool:
    if any(
        atoms.intersection(first_atoms) and atoms.intersection(second_atoms)
        for first_atoms, second_atoms in _SENSITIVE_COMPOUND_ATOMS
    ):
        return True
    if complete_token and atoms.intersection(_SENSITIVE_SINGLE_ATOMS):
        return True
    return atom_count > 1 and bool(
        atoms.intersection(_SENSITIVE_SINGLE_ATOMS | _QUALIFIABLE_WHOLE_ATOMS)
    )


def _edge_spanning_atoms_are_sensitive(
    token: str,
    runs: tuple[_SemanticAtomRun, ...],
) -> bool:
    start_atoms = frozenset(
        atom for run in runs if run.start == 0 for atom in run.atoms
    )
    end_atoms = frozenset(
        atom for run in runs if run.end == len(token) for atom in run.atoms
    )
    if token not in _ORDINARY_PARTIAL_ATOM_TOKENS and end_atoms.intersection(
        _SENSITIVE_SINGLE_ATOMS
    ):
        return True
    for run in runs:
        if run.start != 0:
            continue
        for atom in run.atoms.intersection(_SENSITIVE_SINGLE_ATOMS):
            if not any(
                token.startswith(ordinary_lexeme) and atom in ordinary_lexeme
                for ordinary_lexeme in _ORDINARY_SEMANTIC_HOMOGRAPHS
            ):
                return True
    return any(
        (start_atoms.intersection(first_atoms) and end_atoms.intersection(second_atoms))
        or (
            start_atoms.intersection(second_atoms)
            and end_atoms.intersection(first_atoms)
        )
        for first_atoms, second_atoms in _SENSITIVE_COMPOUND_ATOMS
    )


def _contains_sensitive_semantic_atoms(
    raw_tokens: tuple[str, ...],
    atom_runs_by_token: Mapping[str, tuple[_SemanticAtomRun, ...]],
) -> bool:
    complete_atoms: set[str] = set()
    complete_atom_count = 0
    for token in raw_tokens:
        runs = atom_runs_by_token[token]
        if _edge_spanning_atoms_are_sensitive(token, runs):
            return True
        for run in runs:
            complete_token = run.start == 0 and run.end == len(token)
            if _semantic_atoms_are_sensitive(
                run.atoms,
                atom_count=run.atom_count,
                complete_token=complete_token,
            ):
                return True
            if complete_token or (
                run.end == len(token) and token not in _ORDINARY_PARTIAL_ATOM_TOKENS
            ):
                complete_atoms.update(run.atoms)
                complete_atom_count += run.atom_count
    return _semantic_atoms_are_sensitive(
        frozenset(complete_atoms),
        atom_count=complete_atom_count,
        complete_token=True,
    )


def _contains_qualified_safe_compound(
    value: str,
    runs: tuple[_SemanticAtomRun, ...],
) -> bool:
    for run in runs:
        safe_atoms = _SAFE_COMPOUND_WHOLE_LEXEMES.intersection(run.atoms)
        has_ordinary_suffix = any(
            any(
                value[run.end :].startswith(suffix)
                for suffix in _ORDINARY_SAFE_COMPOUND_SUFFIXES.get(atom, ())
            )
            for atom in safe_atoms
        )
        if (
            safe_atoms
            and ((run.start == 0 and run.atom_count > 1) or run.start > 0)
            and run.end == len(value)
        ) or (
            safe_atoms
            and run.start == 0
            and run.end < len(value)
            and not has_ordinary_suffix
        ):
            return True
    return False


def _mask_ordinary_semantic_lexemes(token: str) -> str:
    masked = token
    for ordinary_lexeme in _ORDINARY_SEMANTIC_HOMOGRAPHS:
        masked = masked.replace(ordinary_lexeme, "#" * len(ordinary_lexeme))
    return masked


def _contains_sensitive_character_stream(raw_tokens: tuple[str, ...]) -> bool:
    stream = "".join(_mask_ordinary_semantic_lexemes(token) for token in raw_tokens)
    if any(atom in stream for atom in _SENSITIVE_SINGLE_ATOMS):
        return True
    return any(
        any(atom in stream for atom in first_atoms)
        and any(atom in stream for atom in second_atoms)
        for first_atoms, second_atoms in _SENSITIVE_COMPOUND_ATOMS
    )


def _classify_semantic_key(value: str) -> _SemanticKeyClass:
    if (
        type(value) is not str
        or not value
        or not value.isascii()
        or len(value) > MAX_PUBLIC_DATA_KEY_LENGTH
        or value.startswith("_")
    ):
        return _SemanticKeyClass.SENSITIVE
    tokens = _semantic_key_tokens(value)
    normalized = "".join(tokens)
    if not normalized:
        return _SemanticKeyClass.SENSITIVE
    if normalized in _SAFE_WHOLE_LEXEMES:
        return _SemanticKeyClass.SAFE_WHOLE_LEXEME
    if normalized in _ORDINARY_WHOLE_LEXEMES:
        return _SemanticKeyClass.ORDINARY_PUBLIC
    if normalized in _SENSITIVE_WHOLE_LEXEMES:
        return _SemanticKeyClass.SENSITIVE
    plural_normalized = _normalize_semantic_plurals(normalized)
    if plural_normalized in _SENSITIVE_WHOLE_LEXEMES:
        return _SemanticKeyClass.SENSITIVE
    if plural_normalized in _SAFE_WHOLE_LEXEMES:
        return _SemanticKeyClass.SAFE_WHOLE_LEXEME
    if plural_normalized in _ORDINARY_WHOLE_LEXEMES:
        return _SemanticKeyClass.ORDINARY_PUBLIC
    semantic_tokens = tuple(_mask_ordinary_semantic_lexemes(token) for token in tokens)
    atom_runs_by_token = {
        token: _semantic_atom_runs(token) for token in {*semantic_tokens, normalized}
    }
    if (
        _contains_qualified_safe_compound(
            normalized,
            atom_runs_by_token[normalized],
        )
        or _contains_sensitive_semantic_atoms(semantic_tokens, atom_runs_by_token)
        or _contains_sensitive_character_stream(semantic_tokens)
    ):
        return _SemanticKeyClass.SENSITIVE
    return _SemanticKeyClass.ORDINARY_PUBLIC


def _looks_like_legacy_numeric_hostname(value: str) -> bool:
    parts = value.split(".")
    return bool(parts) and all(
        re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)", part) is not None for part in parts
    )


def _public_dns_hostname(value: str) -> bool:
    if not value.isascii() or len(value) > 253:
        return False
    labels = value.split(".")
    if (
        len(labels) < 2
        or labels[-1] in _NON_PUBLIC_DNS_SUFFIXES
        or labels[-1].isdigit()
        or any(
            value == name or value.endswith(f".{name}")
            for name in _NON_PUBLIC_DNS_NAMES
        )
    ):
        return False
    return all(
        0 < len(label) <= 63
        and label[0].isalnum()
        and label[-1].isalnum()
        and all(character.isalnum() or character == "-" for character in label)
        for label in labels
    )


def _public_authority_hostname(value: str) -> bool:
    if "%" in value or value in _IANA_NON_GLOBAL_IP_LITERALS:
        return False
    try:
        address = ip_address(value)
    except ValueError:
        return not _looks_like_legacy_numeric_hostname(value) and _public_dns_hostname(
            value
        )
    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
        return False
    return (
        address.is_global
        and not address.is_link_local
        and not address.is_loopback
        and not address.is_multicast
        and not address.is_private
        and not address.is_reserved
        and not (isinstance(address, IPv6Address) and address.is_site_local)
        and not address.is_unspecified
    )


def _percent_escapes_are_safe(value: str) -> bool:
    index = 0
    while (index := value.find("%", index)) >= 0:
        if (
            index + 2 >= len(value)
            or re.fullmatch(r"[0-9A-Fa-f]{2}", value[index + 1 : index + 3]) is None
        ):
            return False
        decoded = int(value[index + 1 : index + 3], 16)
        if decoded < 0x20 or decoded == 0x7F:
            return False
        index += 3
    return True


def _contains_c0_control(value: str) -> bool:
    return any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)


def _closed_percent_decode(value: str) -> str | None:
    decoded = value
    for _ in range(MAX_PUBLIC_DATA_DEPTH + 1):
        if "%" not in decoded:
            return decoded
        if not _percent_escapes_are_safe(decoded):
            return None
        try:
            next_decoded = unquote(decoded, encoding="utf-8", errors="strict")
        except UnicodeDecodeError:
            return None
        if next_decoded == decoded or _contains_c0_control(next_decoded):
            return None
        decoded = next_decoded
    return None


def _decoded_parameter_value_is_safe(
    value: str,
    *,
    nested_locators: list[tuple[str, int]],
    depth: int,
) -> bool:
    if _FILE_URI_PATTERN.search(value) is not None:
        return False
    if _SCHEME_PATTERN.search(value) is not None:
        if depth >= MAX_PUBLIC_DATA_DEPTH:
            return False
        nested_locators.append((value, depth + 1))
    return True


def _decoded_parameter_path_is_safe(value: str) -> bool:
    if _SCHEME_PATTERN.search(value) is not None:
        return True
    if (
        value.startswith(("//", "\\"))
        or _UNC_PATH_PATTERN.search(value) is not None
        or _WINDOWS_DRIVE_PATH_PATTERN.search(value) is not None
    ):
        return False
    return not _persisted_string_has_local_path(
        value,
        (),
        allow_public_rooted_routes=True,
    )


def _decoded_parameter_is_safe(
    name: str,
    value: str,
    *,
    nested_locators: list[tuple[str, int]],
    depth: int,
) -> bool:
    closed_name = _closed_percent_decode(name)
    closed_value = _closed_percent_decode(value)
    if closed_name is None or closed_value is None:
        return False
    normalized_name = normalize("NFKC", closed_name)
    normalized_value = normalize("NFKC", closed_value)
    return (
        not _contains_c0_control(normalized_name)
        and not _contains_c0_control(normalized_value)
        and _decoded_parameter_path_is_safe(normalized_name)
        and _decoded_parameter_path_is_safe(normalized_value)
        and _classify_semantic_key(name) is not _SemanticKeyClass.SENSITIVE
        and _classify_semantic_key(closed_name) is not _SemanticKeyClass.SENSITIVE
        and _decoded_parameter_value_is_safe(
            normalized_name,
            nested_locators=nested_locators,
            depth=depth,
        )
        and _decoded_parameter_value_is_safe(
            normalized_value,
            nested_locators=nested_locators,
            depth=depth,
        )
    )


def _locator_parameters_are_safe(
    value: str,
    *,
    nested_locators: list[tuple[str, int]],
    depth: int,
) -> bool:
    if not value or not _percent_escapes_are_safe(value):
        return not value
    try:
        parameters = parse_qsl(
            value.replace(";", "&"),
            keep_blank_values=True,
            max_num_fields=MAX_PUBLIC_DATA_ITEMS,
            encoding="utf-8",
            errors="strict",
        )
    except (UnicodeDecodeError, ValueError):
        return False
    return all(
        _decoded_parameter_is_safe(
            name,
            item,
            nested_locators=nested_locators,
            depth=depth,
        )
        for name, item in parameters
    )


def _scheme_starts_at_token_boundary(value: str, index: int) -> bool:
    return index == 0 or not (value[index - 1].isalnum() or value[index - 1] in "+-.")


def _is_invalid_locator_character(value: str) -> bool:
    return any(
        character in _INVALID_LOCATOR_CHARACTERS
        for character in normalize("NFKC", value)
    )


def _starts_absolute_path(value: str, index: int) -> bool:
    if index >= len(value):
        return False
    if value.startswith(("/", "\\"), index):
        return True
    if value.startswith("..", index):
        after_parent = index + 2
        if after_parent == len(value) or value[after_parent] in "/\\":
            return True
    return (
        index + 2 < len(value)
        and value[index].isascii()
        and value[index].isalpha()
        and value[index + 1] == ":"
        and value[index + 2] in "/\\"
    )


def _is_nested_http_scheme(value: str, colon: int) -> bool:
    for scheme in ("https", "http"):
        scheme_start = colon - len(scheme)
        if (
            scheme_start >= 0
            and value[scheme_start:colon].casefold() == scheme
            and _scheme_starts_at_token_boundary(value, scheme_start)
        ):
            return True
    return False


def _locator_has_local_path_suffix(path: str, query: str, fragment: str) -> bool:
    normalized_path = normalize("NFKC", path)
    path_index = 0
    while (path_index := normalized_path.find("=", path_index)) >= 0:
        if _starts_absolute_path(normalized_path, path_index + 1):
            return True
        path_index += 1
    visible = normalize(
        "NFKC",
        path + (f"?{query}" if query else "") + (f"#{fragment}" if fragment else ""),
    )
    index = 0
    while index < len(visible):
        if visible[index] not in _LOCAL_PATH_SUFFIX_DELIMITERS:
            index += 1
            continue
        if _starts_absolute_path(visible, index):
            return True
        if visible[index] == ":" and _is_nested_http_scheme(visible, index):
            index += 1
            continue
        cursor = index + 1
        if _starts_absolute_path(visible, cursor):
            return True
        while (
            cursor < len(visible)
            and visible[cursor] not in "=/\\&?#"
            and ord(visible[cursor]) > 0x20
            and ord(visible[cursor]) != 0x7F
        ):
            if visible[
                cursor
            ] in _LOCAL_PATH_SUFFIX_DELIMITERS and _starts_absolute_path(
                visible, cursor + 1
            ):
                return True
            cursor += 1
        if (
            cursor < len(visible)
            and visible[cursor] == "="
            and _starts_absolute_path(visible, cursor + 1)
        ):
            return True
        index = max(index + 1, cursor + 1)
    return False


def _locator_authority(value: str, scheme_end: int) -> tuple[int, str]:
    authority_end = scheme_end
    while (
        authority_end < len(value)
        and value[authority_end] not in _AUTHORITY_TERMINATORS
        and ord(value[authority_end]) > 0x20
        and ord(value[authority_end]) != 0x7F
    ):
        authority_end += 1
    return authority_end, value[scheme_end:authority_end]


def _locator_token(value: str, start: int, scheme_end: int) -> tuple[int, str, str]:
    authority_end, authority = _locator_authority(value, scheme_end)
    token_end = authority_end
    if token_end < len(value) and value[token_end].isspace():
        continuation = value[token_end:].lstrip()
        if continuation.startswith("@"):
            raise ValueError("result artifact persisted document is invalid")
    if token_end < len(value) and value[token_end] in "/?#":
        token_end += 1
        while (
            token_end < len(value)
            and not _is_invalid_locator_character(value[token_end])
            and ord(value[token_end]) > 0x20
            and ord(value[token_end]) != 0x7F
        ):
            token_end += 1
    if token_end < len(value) and _is_invalid_locator_character(value[token_end]):
        raise ValueError("result artifact persisted document is invalid")
    return token_end, value[start:token_end], authority


def _authority_brackets_are_exact(authority: str) -> bool:
    if not authority.startswith("["):
        return "[" not in authority and "]" not in authority
    closing = authority.find("]")
    if closing < 0 or "[" in authority[1:closing] or "]" in authority[closing + 1 :]:
        return False
    remainder = authority[closing + 1 :]
    return not remainder or re.fullmatch(r":[0-9]+", remainder) is not None


def _decoded_locator_path_is_safe(
    path: str,
    *,
    nested_locators: list[tuple[str, int]],
    depth: int,
) -> bool:
    decoded_path = _closed_percent_decode(path)
    if decoded_path is None:
        return False
    visible = normalize("NFKC", decoded_path)
    if not visible:
        return True
    if visible.startswith("//") or _locator_has_local_path_suffix(visible, "", ""):
        return False
    body = visible[1:] if visible.startswith("/") else visible
    if (
        _FILE_URI_PATTERN.search(body) is not None
        or _UNC_PATH_PATTERN.search(body) is not None
        or _WINDOWS_DRIVE_PATH_PATTERN.search(body) is not None
        or ".." in body.replace("\\", "/").split("/")
    ):
        return False
    scheme_matches = tuple(_SCHEME_PATTERN.finditer(body))
    if not scheme_matches:
        return not _persisted_string_has_local_path(body, ())
    for match in scheme_matches:
        if not _scheme_starts_at_token_boundary(body, match.start()):
            return False
        authority_end, authority = _locator_authority(body, match.end())
        try:
            _validate_public_locator(
                body[match.start() : authority_end],
                authority,
                nested_locators=nested_locators,
                depth=depth,
            )
        except ValueError:
            return False
    if visible != path:
        if depth >= MAX_PUBLIC_DATA_DEPTH:
            return False
        nested_locators.append((body, depth + 1))
    return True


def _validate_public_locator(
    token: str,
    authority: str,
    *,
    nested_locators: list[tuple[str, int]],
    depth: int,
) -> None:
    if (
        not authority
        or not _authority_brackets_are_exact(authority)
        or "\\" in authority
        or "%" in authority
        or any(ord(character) <= 0x20 or ord(character) == 0x7F for character in token)
    ):
        raise ValueError("result artifact persisted document is invalid")
    try:
        parsed = urlsplit(token)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ValueError("result artifact persisted document is invalid") from None
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or parsed.netloc != authority
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or port == 0
        or authority.endswith(":")
    ):
        raise ValueError("result artifact persisted document is invalid")

    normalized_hostname = hostname.casefold()
    if normalized_hostname.endswith(".."):
        raise ValueError("result artifact persisted document is invalid")
    has_root_dot = normalized_hostname.endswith(".")
    if has_root_dot:
        normalized_hostname = normalized_hostname[:-1]
    if not normalized_hostname:
        raise ValueError("result artifact persisted document is invalid")
    if has_root_dot:
        try:
            ip_address(normalized_hostname)
        except ValueError:
            pass
        else:
            raise ValueError("result artifact persisted document is invalid") from None
    if authority.startswith("["):
        try:
            ip_address(normalized_hostname)
        except ValueError:
            raise ValueError("result artifact persisted document is invalid") from None
    if not _public_authority_hostname(normalized_hostname):
        raise ValueError("result artifact persisted document is invalid")
    if any(
        not _percent_escapes_are_safe(part)
        for part in (parsed.path, parsed.query, parsed.fragment)
    ):
        raise ValueError("result artifact persisted document is invalid")
    if _locator_has_local_path_suffix(
        parsed.path,
        parsed.query,
        parsed.fragment,
    ) or not _decoded_locator_path_is_safe(
        parsed.path,
        nested_locators=nested_locators,
        depth=depth,
    ):
        raise ValueError("result artifact persisted document is invalid")
    if not _locator_parameters_are_safe(
        parsed.query,
        nested_locators=nested_locators,
        depth=depth,
    ) or not _locator_parameters_are_safe(
        parsed.fragment,
        nested_locators=nested_locators,
        depth=depth,
    ):
        raise ValueError("result artifact persisted document is invalid")


def _public_locator_spans(
    value: str,
    *,
    nested_locators: list[tuple[str, int]],
    depth: int,
) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
    covered_until = 0
    for match in _SCHEME_PATTERN.finditer(value):
        if match.start() < covered_until:
            if _scheme_starts_at_token_boundary(value, match.start()):
                authority_end, authority = _locator_authority(value, match.end())
                _validate_public_locator(
                    value[match.start() : authority_end],
                    authority,
                    nested_locators=nested_locators,
                    depth=depth,
                )
            continue
        if not _scheme_starts_at_token_boundary(value, match.start()):
            continue
        token_end, token, authority = _locator_token(value, match.start(), match.end())
        _validate_public_locator(
            token,
            authority,
            nested_locators=nested_locators,
            depth=depth,
        )
        spans.append((match.start(), token_end))
        covered_until = token_end
    return tuple(spans)


def _persisted_string_has_local_path(
    value: str,
    locator_spans: tuple[tuple[int, int], ...],
    *,
    allow_public_rooted_routes: bool = False,
) -> bool:
    def at_path_boundary(index: int) -> bool:
        if index == 0:
            return True
        previous = value[index - 1]
        return not (previous.isalnum() or previous in "/\\")

    def public_rooted_route_starts(index: int) -> bool:
        for prefix in _PUBLIC_ROOTED_ROUTE_PREFIXES:
            if not value.startswith(prefix, index):
                continue
            route_end = index + len(prefix)
            if route_end == len(value) or value[route_end] == "/":
                return True
        return False

    locator_index = 0
    for index, character in enumerate(value):
        while (
            locator_index < len(locator_spans)
            and locator_spans[locator_index][1] <= index
        ):
            locator_index += 1
        if (
            locator_index < len(locator_spans)
            and locator_spans[locator_index][0] <= index
        ):
            continue
        if (
            index + 2 < len(value)
            and at_path_boundary(index)
            and character.isascii()
            and character.isalpha()
            and value[index + 1] == ":"
            and value[index + 2] in {"/", "\\"}
        ):
            return True
        if character == "/":
            following = value[index + 1] if index + 1 < len(value) else ""
            if (
                at_path_boundary(index)
                and (index == 0 or not following or not following.isspace())
                and not (
                    allow_public_rooted_routes and public_rooted_route_starts(index)
                )
            ):
                return True
        elif character == "\\":
            following = value[index + 1] if index + 1 < len(value) else ""
            after_pair = value[index + 2] if index + 2 < len(value) else ""
            if following == "\\" and (not after_pair or after_pair.isspace()):
                continue
            if at_path_boundary(index) and (
                index == 0 or not following or not following.isspace()
            ):
                return True

    normalized = value.replace("\\", "/")
    start = 0
    while (index := normalized.find("..", start)) >= 0:
        previous = normalized[index - 1] if index else ""
        following = normalized[index + 2] if index + 2 < len(normalized) else ""
        if (index == 0 or previous == "/" or at_path_boundary(index)) and (
            not following or following == "/"
        ):
            return True
        start = index + 2
    return False


def _unsafe_persisted_string(value: str) -> bool:
    nested_locators = [(value, 0)]
    while nested_locators:
        current, depth = nested_locators.pop()
        try:
            percent_decoded = unquote(current, encoding="utf-8", errors="strict")
        except UnicodeDecodeError:
            return True
        if _contains_c0_control(percent_decoded):
            return True
        if percent_decoded != current:
            if depth >= MAX_PUBLIC_DATA_DEPTH:
                return True
            nested_locators.append((percent_decoded, depth + 1))
        compatibility_normalized = normalize("NFKC", current)
        if compatibility_normalized != current and (
            _contains_c0_control(compatibility_normalized)
            or _FILE_URI_PATTERN.search(compatibility_normalized) is not None
            or _UNC_PATH_PATTERN.search(compatibility_normalized) is not None
            or _persisted_string_has_local_path(compatibility_normalized, ())
        ):
            return True
        if (
            _contains_c0_control(current)
            or _FILE_URI_PATTERN.search(current) is not None
            or _UNC_PATH_PATTERN.search(current) is not None
        ):
            return True
        try:
            locator_spans = _public_locator_spans(
                current,
                nested_locators=nested_locators,
                depth=depth,
            )
        except ValueError:
            return True
        if _persisted_string_has_local_path(current, locator_spans):
            return True
    return False


def _validate_detached_json(value: object) -> None:
    pending = [value]
    validated: set[int] = set()
    while pending:
        current = pending.pop()
        if type(current) in {dict, list, str}:
            identity = id(current)
            if identity in validated:
                continue
            validated.add(identity)
        if type(current) is dict:
            for key, nested in cast(dict[str, object], current).items():
                if _classify_semantic_key(
                    key
                ) is _SemanticKeyClass.SENSITIVE or _unsafe_persisted_string(key):
                    raise ValueError("result artifact persisted document is invalid")
                pending.append(nested)
        elif type(current) is list:
            pending.extend(cast(list[object], current))
        elif type(current) is str and _unsafe_persisted_string(current):
            raise ValueError("result artifact persisted document is invalid")


def _append_canonical_json_line(destination: bytearray, value: object) -> None:
    for chunk in _CANONICAL_JSON_ENCODER.iterencode(value):
        encoded_chunk = chunk.encode("utf-8")
        if len(destination) + len(encoded_chunk) + 1 > _MAX_FILE_BYTES:
            raise ValueError("result artifact persisted document is invalid")
        destination.extend(encoded_chunk)
    if len(destination) + 1 > _MAX_FILE_BYTES:
        raise ValueError("result artifact persisted document is invalid")
    destination.append(0x0A)


def _prepare_completed_partition(
    *,
    _prepared_builder: Callable[..., PreparedResultArtifactFile],
    kind: str,
    exchange: str,
    shard_index: int,
    schema_version: str,
    records: Sequence[Mapping[str, object]],
) -> PreparedResultArtifactFile:
    """Prepare one canonical completed JSONL partition without persistence."""

    if (
        type(kind) is not str
        or kind not in {"factors", "ranking", "universe"}
        or type(exchange) is not str
        or exchange not in {"BSE", "SSE", "SZSE"}
        or type(shard_index) is not int
        or not 0 <= shard_index < _MAX_SHARD_COUNT
        or not _valid_schema_token(schema_version)
    ):
        raise ValueError("result artifact partition coordinates are invalid")
    try:
        detached = _detached_json_value(records)
        if type(detached) is not list or len(detached) > _MAX_PARTITION_RECORDS:
            raise ValueError("result artifact partition records are invalid")
        encoded_buffer = bytearray()
        for record in cast(list[object], detached):
            if type(record) is not dict:
                raise ValueError("result artifact partition records are invalid")
            _validate_detached_json(record)
            _append_canonical_json_line(encoded_buffer, record)
        encoded = bytes(encoded_buffer)
    except MemoryError:
        raise MemoryError(
            "result artifact partition preparation exhausted memory"
        ) from None
    except Exception:
        raise ValueError("result artifact partition records are invalid") from None
    return _prepared_builder(
        relative_path=(f"partitions/{kind}/{exchange}/part-{shard_index:05d}.jsonl"),
        schema_version=schema_version,
        content_bytes=encoded,
        record_count=len(cast(list[object], detached)),
    )


def _prepare_candidate(
    *,
    _prepared_builder: Callable[..., PreparedResultArtifactFile],
    canonical_security_id: str,
    schema_version: str,
    document: Mapping[str, object],
) -> PreparedResultArtifactFile:
    """Prepare one canonical per-security JSON document without persistence."""

    if type(canonical_security_id) is not str:
        raise ValueError("result artifact candidate coordinates are invalid")
    exchange, delimiter, code = canonical_security_id.partition(":")
    if (
        exchange not in {"BSE", "SSE", "SZSE"}
        or delimiter != ":"
        or len(code) != 6
        or not code.isascii()
        or not code.isdigit()
        or not _valid_schema_token(schema_version)
    ):
        raise ValueError("result artifact candidate coordinates are invalid")
    try:
        detached = _detached_json_value(document)
        if type(detached) is not dict:
            raise ValueError("result artifact candidate document is invalid")
        _validate_detached_json(detached)
        encoded_buffer = bytearray()
        _append_canonical_json_line(encoded_buffer, detached)
        encoded = bytes(encoded_buffer)
    except MemoryError:
        raise MemoryError(
            "result artifact candidate preparation exhausted memory"
        ) from None
    except Exception:
        raise ValueError("result artifact candidate document is invalid") from None
    return _prepared_builder(
        relative_path=f"candidates/{exchange}-{code}.json",
        schema_version=schema_version,
        content_bytes=encoded,
        record_count=1,
    )


def _bind_prepared_factories(
    prepared_builder: Callable[..., PreparedResultArtifactFile],
) -> tuple[
    Callable[..., PreparedResultArtifactFile],
    Callable[..., PreparedResultArtifactFile],
]:
    def prepare_candidate(
        *,
        canonical_security_id: str,
        schema_version: str,
        document: Mapping[str, object],
    ) -> PreparedResultArtifactFile:
        return _prepare_candidate(
            _prepared_builder=prepared_builder,
            canonical_security_id=canonical_security_id,
            schema_version=schema_version,
            document=document,
        )

    def prepare_completed_partition(
        *,
        kind: str,
        exchange: str,
        shard_index: int,
        schema_version: str,
        records: Sequence[Mapping[str, object]],
    ) -> PreparedResultArtifactFile:
        return _prepare_completed_partition(
            _prepared_builder=prepared_builder,
            kind=kind,
            exchange=exchange,
            shard_index=shard_index,
            schema_version=schema_version,
            records=records,
        )

    return prepare_candidate, prepare_completed_partition


prepare_candidate, prepare_completed_partition = _bind_prepared_factories(
    _prepared_file
)
del _prepared_file
del _prepared_values_lookup
del _prepared_instance_check
del _register_prepared
del _build_prepared_registry
del _SEALED_PREPARED_TYPES
del _bind_prepared_factories


__all__ = [
    "PreparedResultArtifactFile",
    "prepare_candidate",
    "prepare_completed_partition",
]
