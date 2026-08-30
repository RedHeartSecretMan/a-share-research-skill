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
from ipaddress import IPv6Address, ip_address
from typing import NamedTuple, cast
from unicodedata import normalize
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


class _SemanticKeyClass(Enum):
    ORDINARY_PUBLIC = auto()
    SAFE_WHOLE_LEXEME = auto()
    SENSITIVE = auto()


class _SemanticAtomRun(NamedTuple):
    start: int
    end: int
    atoms: tuple[str, ...]


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
_SEMANTIC_ATOMS = tuple(
    sorted(
        {
            "access",
            "account",
            "address",
            "annual",
            "api",
            "application",
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


def _normalize_semantic_plurals(value: str) -> str:
    normalized = value
    for plural, singular in _SEMANTIC_PLURAL_REPLACEMENTS:
        normalized = normalized.replace(plural, singular)
    return normalized


def _semantic_atom_runs(token: str) -> tuple[_SemanticAtomRun, ...]:
    memo: dict[int, tuple[int, tuple[str, ...]]] = {}

    def longest_run(start: int) -> tuple[int, tuple[str, ...]]:
        cached = memo.get(start)
        if cached is not None:
            return cached
        best: tuple[int, tuple[str, ...]] = (start, ())
        for lexeme, atom in _SEMANTIC_ATOM_ALIASES:
            if not token.startswith(lexeme, start):
                continue
            end, remainder = longest_run(start + len(lexeme))
            if end > best[0]:
                best = (end, (atom, *remainder))
        memo[start] = best
        return best

    runs: list[_SemanticAtomRun] = []
    for start in range(len(token)):
        cursor, atoms = longest_run(start)
        if atoms:
            runs.append(_SemanticAtomRun(start, cursor, atoms))
    return tuple(runs)


def _semantic_atoms_are_sensitive(
    atoms: tuple[str, ...],
    *,
    complete_token: bool,
) -> bool:
    atom_set = frozenset(atoms)
    if any(
        atom_set.intersection(first_atoms) and atom_set.intersection(second_atoms)
        for first_atoms, second_atoms in _SENSITIVE_COMPOUND_ATOMS
    ):
        return True
    if complete_token and atom_set.intersection(_SENSITIVE_SINGLE_ATOMS):
        return True
    return len(atoms) > 1 and bool(
        atom_set.intersection(_SENSITIVE_SINGLE_ATOMS | _QUALIFIABLE_WHOLE_ATOMS)
    )


def _contains_sensitive_semantic_atoms(raw_tokens: tuple[str, ...]) -> bool:
    complete_atoms: list[str] = []
    for token in raw_tokens:
        for run in _semantic_atom_runs(token):
            complete_token = run.start == 0 and run.end == len(token)
            if _semantic_atoms_are_sensitive(
                run.atoms,
                complete_token=complete_token,
            ):
                return True
            if complete_token:
                complete_atoms.extend(run.atoms)
    return _semantic_atoms_are_sensitive(
        tuple(complete_atoms),
        complete_token=True,
    )


def _contains_qualified_safe_compound(value: str) -> bool:
    for run in _semantic_atom_runs(value):
        if (
            run.start == 0
            and run.end == len(value)
            and len(run.atoms) > 1
            and _SAFE_COMPOUND_WHOLE_LEXEMES.intersection(run.atoms)
        ):
            return True
    return False


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
    if _contains_qualified_safe_compound(
        normalized
    ) or _contains_sensitive_semantic_atoms(tokens):
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
    normalized_name = normalize("NFKC", name)
    normalized_value = normalize("NFKC", value)
    return (
        not _contains_c0_control(normalized_name)
        and not _contains_c0_control(normalized_value)
        and _decoded_parameter_path_is_safe(normalized_name)
        and _decoded_parameter_path_is_safe(normalized_value)
        and _classify_semantic_key(normalized_name) is not _SemanticKeyClass.SENSITIVE
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
    if _locator_has_local_path_suffix(parsed.path, parsed.query, parsed.fragment):
        raise ValueError("result artifact persisted document is invalid")
    if any(
        not _percent_escapes_are_safe(part)
        for part in (parsed.path, parsed.query, parsed.fragment)
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
