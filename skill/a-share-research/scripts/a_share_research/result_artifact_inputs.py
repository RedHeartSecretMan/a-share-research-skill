"""Prepare immutable, filesystem-free inputs for Result Artifact persistence."""

from __future__ import annotations

import json
import math
import re
from abc import ABC, ABCMeta, abstractmethod
from collections.abc import Mapping, Sequence
from decimal import Decimal
from enum import Enum, auto
from hashlib import sha256
from ipaddress import ip_address
from typing import NamedTuple, cast
from urllib.parse import parse_qsl, urlsplit
from weakref import ReferenceType, ref

from .bounded_public_data import (
    MAX_PUBLIC_DATA_DEPTH,
    MAX_PUBLIC_DATA_ITEMS,
    MAX_PUBLIC_DATA_KEY_LENGTH,
    MAX_PUBLIC_DATA_STRING_LENGTH,
)

_MAX_FILE_BYTES = 64 * 1024 * 1024
_MAX_PARTITION_RECORDS = 100_000
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
_FILE_URI_PATTERN = re.compile(r"(?i:file://)")
_UNC_PATH_PATTERN = re.compile(r"\\{2,}[^\\/\s]+\\[^\\/\s]+")
_AUTHORITY_TERMINATORS = frozenset("/?#")
_INVALID_LOCATOR_CHARACTERS = frozenset('<>"|\\^`{}')
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


class _SemanticKeyClass(Enum):
    ORDINARY_PUBLIC = auto()
    SAFE_WHOLE_LEXEME = auto()
    SENSITIVE = auto()


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
_SENSITIVE_SINGLE_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"authorizations?",
        r"cookies?",
        r"credentials?",
        r"passwords?",
        r"secrets?",
        r"tokens?",
    )
)
_ORDINARY_EMBEDDED_LEXEMES = re.compile(r"secretar(?:iat(?:s)?|y|ies|ial)")
_SENSITIVE_COMPOUND_ATOMS: tuple[
    tuple[tuple[str, ...], tuple[str, ...]],
    ...,
] = (
    (("api", "apis"), ("key", "keys")),
    (("raw",), ("payload", "payloads")),
    (("request", "requests"), ("header", "headers")),
    (
        ("server", "servers"),
        (
            "address",
            "addresses",
            "detail",
            "details",
            "info",
            "infos",
            "information",
            "informations",
            "ip",
            "ips",
            "metadata",
            "name",
            "names",
            "port",
            "ports",
            "url",
            "urls",
        ),
    ),
)


class _PreparedValues(NamedTuple):
    relative_path: str
    schema_version: str
    content_bytes: bytes
    sha256: str
    byte_count: int
    record_count: int


_MODULE_AUTHORITY = object()


class _PreparedResultArtifactFileMeta(ABCMeta):
    def __instancecheck__(cls, instance: object) -> bool:
        if type(instance) is not _PreparedResultArtifactFile:
            return False
        registered = _PREPARED_VALUES.get(id(instance))
        return registered is not None and registered[0]() is instance


class PreparedResultArtifactFile(ABC, metaclass=_PreparedResultArtifactFileMeta):
    """Read-only Interface to one Module-owned prepared file value."""

    __slots__ = ()

    def __init_subclass__(
        cls,
        *,
        _module_authority: object | None = None,
        **kwargs: object,
    ) -> None:
        if _module_authority is not _MODULE_AUTHORITY or kwargs:
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


class _PreparedResultArtifactFile(
    PreparedResultArtifactFile,
    _module_authority=_MODULE_AUTHORITY,
):
    __slots__ = ("__weakref__",)

    def __new__(cls) -> _PreparedResultArtifactFile:
        raise TypeError("prepared result artifact files are Module-owned")

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


_PREPARED_VALUES: dict[
    int,
    tuple[ReferenceType[_PreparedResultArtifactFile], _PreparedValues],
] = {}


def _prepared_values(value: PreparedResultArtifactFile) -> _PreparedValues:
    registered = _PREPARED_VALUES.get(id(value))
    if registered is None or registered[0]() is not value:
        raise TypeError("prepared result artifact file is not Module-owned")
    return registered[1]


def _prepared_file(
    *,
    relative_path: str,
    schema_version: str,
    content_bytes: bytes,
    record_count: int,
) -> PreparedResultArtifactFile:
    values = _PreparedValues(
        relative_path=relative_path,
        schema_version=schema_version,
        content_bytes=content_bytes,
        sha256=sha256(content_bytes).hexdigest(),
        byte_count=len(content_bytes),
        record_count=record_count,
    )
    prepared = object.__new__(_PreparedResultArtifactFile)
    identifier = id(prepared)

    def discard(dead_reference: ReferenceType[_PreparedResultArtifactFile]) -> None:
        registered = _PREPARED_VALUES.get(identifier)
        if registered is not None and registered[0] is dead_reference:
            del _PREPARED_VALUES[identifier]

    reference = ref(prepared, discard)
    _PREPARED_VALUES[identifier] = (reference, values)
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
            if not decimal_item.is_finite():
                raise ValueError("result artifact persisted document is invalid")
            return str(decimal_item), 1, 0

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


def _contains_ordered_atoms(
    value: str,
    start_atoms: tuple[str, ...],
    end_atoms: tuple[str, ...],
) -> bool:
    for start_atom in start_atoms:
        search_from = 0
        while (start := value.find(start_atom, search_from)) >= 0:
            after_start = start + len(start_atom)
            if any(value.find(end_atom, after_start) >= 0 for end_atom in end_atoms):
                return True
            search_from = start + 1
    return False


def _contains_sensitive_compound(value: str) -> bool:
    return any(
        _contains_ordered_atoms(value, start_atoms, end_atoms)
        for start_atoms, end_atoms in _SENSITIVE_COMPOUND_ATOMS
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
    ordinary_elided = _ORDINARY_EMBEDDED_LEXEMES.sub("", normalized)
    if (
        normalized in _SENSITIVE_WHOLE_LEXEMES
        or _contains_sensitive_compound(normalized)
        or any(
            pattern.search(ordinary_elided) is not None
            for pattern in _SENSITIVE_SINGLE_PATTERNS
        )
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
    if "%" in value:
        return False
    try:
        address = ip_address(value)
    except ValueError:
        return not _looks_like_legacy_numeric_hostname(value) and _public_dns_hostname(
            value
        )
    return (
        address.is_global
        and not address.is_link_local
        and not address.is_loopback
        and not address.is_multicast
        and not address.is_private
        and not address.is_reserved
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


def _locator_parameters_are_safe(value: str) -> bool:
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
        not _contains_c0_control(name)
        and not _contains_c0_control(parameter_value)
        and _classify_semantic_key(name) is not _SemanticKeyClass.SENSITIVE
        for name, parameter_value in parameters
    )


def _starts_local_path_suffix(value: str, index: int) -> bool:
    if value[index] not in "),;":
        return False
    cursor = index + 1
    if cursor < len(value) and value[cursor] in ",;":
        cursor += 1
    name_start = cursor
    if cursor < len(value) and (
        value[cursor].isascii() and (value[cursor].isalpha() or value[cursor] == "_")
    ):
        cursor += 1
        while cursor < len(value) and (
            value[cursor].isascii()
            and (value[cursor].isalnum() or value[cursor] == "_")
        ):
            cursor += 1
        if cursor < len(value) and value[cursor] == "=":
            cursor += 1
        else:
            cursor = name_start
    remaining = value[cursor:]
    return (
        remaining.startswith(("/", "\\", ".."))
        or len(remaining) >= 3
        and remaining[0].isascii()
        and remaining[0].isalpha()
        and remaining[1] == ":"
        and remaining[2] in "/\\"
    )


def _locator_token(value: str, start: int, scheme_end: int) -> tuple[int, str, str]:
    authority_end = scheme_end
    while (
        authority_end < len(value)
        and value[authority_end] not in _AUTHORITY_TERMINATORS
        and ord(value[authority_end]) > 0x20
        and ord(value[authority_end]) != 0x7F
    ):
        authority_end += 1
    authority = value[scheme_end:authority_end]
    token_end = authority_end
    if token_end < len(value) and value[token_end] in "/?#":
        token_end += 1
        while (
            token_end < len(value)
            and value[token_end] not in _INVALID_LOCATOR_CHARACTERS
            and ord(value[token_end]) > 0x20
            and ord(value[token_end]) != 0x7F
        ):
            if _starts_local_path_suffix(value, token_end):
                raise ValueError("result artifact persisted document is invalid")
            token_end += 1
    if token_end < len(value) and value[token_end] in _INVALID_LOCATOR_CHARACTERS:
        raise ValueError("result artifact persisted document is invalid")
    return token_end, value[start:token_end], authority


def _validate_public_locator(token: str, authority: str) -> None:
    if (
        not authority
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
    if not _locator_parameters_are_safe(
        parsed.query
    ) or not _locator_parameters_are_safe(parsed.fragment):
        raise ValueError("result artifact persisted document is invalid")


def _public_locator_spans(value: str) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
    for match in _SCHEME_PATTERN.finditer(value):
        if match.start() > 0 and (
            value[match.start() - 1].isalnum() or value[match.start() - 1] in "+-."
        ):
            continue
        token_end, token, authority = _locator_token(value, match.start(), match.end())
        _validate_public_locator(token, authority)
        spans.append((match.start(), token_end))
    return tuple(spans)


def _unsafe_persisted_string(value: str) -> bool:
    if (
        _contains_c0_control(value)
        or _FILE_URI_PATTERN.search(value) is not None
        or _UNC_PATH_PATTERN.search(value) is not None
    ):
        return True
    try:
        locator_spans = _public_locator_spans(value)
    except ValueError:
        return True

    def at_path_boundary(index: int) -> bool:
        if index == 0:
            return True
        previous = value[index - 1]
        return not (previous.isalnum() or previous in "/\\")

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
            if at_path_boundary(index) and (
                index == 0 or not following or not following.isspace()
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


def prepare_completed_partition(
    *,
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
        or not 0 <= shard_index < _MAX_PARTITION_RECORDS
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
    return _prepared_file(
        relative_path=(f"partitions/{kind}/{exchange}/part-{shard_index:05d}.jsonl"),
        schema_version=schema_version,
        content_bytes=encoded,
        record_count=len(cast(list[object], detached)),
    )


def prepare_candidate(
    *,
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
    return _prepared_file(
        relative_path=f"candidates/{exchange}-{code}.json",
        schema_version=schema_version,
        content_bytes=encoded,
        record_count=1,
    )


__all__ = [
    "PreparedResultArtifactFile",
    "prepare_candidate",
    "prepare_completed_partition",
]
