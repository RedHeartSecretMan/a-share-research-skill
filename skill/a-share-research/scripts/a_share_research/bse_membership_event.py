"""Target-driven ordinary-share evidence on the dated BSE code-cutover day.

An observed material is source metadata, not caller-filled membership. It is
issued by the counted document observer, then reused only for identical bytes.
This permits a later frozen cutoff without inventing original publication time.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, replace
from datetime import datetime
from functools import partial
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Protocol, cast
from unicodedata import category
from urllib.parse import urlsplit
from weakref import WeakKeyDictionary

from ._universe_http import is_permission_denied
from .bse_code_mapping_source import (
    CODE_CHANGE_EFFECTIVE_DATE,
    BseCodeMappingRecord,
    acquire_bse_code_mapping,
)
from .identity_sources import (
    HttpResponse,
    HttpTransport,
    TransportError,
    UrlLibTransport,
)
from .provider_lane import (
    ProductionInvocationAdapter,
    ProviderInvocation,
    ProviderLanePolicy,
    ProviderLaneResult,
    ProviderLanes,
)
from .universe_input import (
    ExchangeListOutcome,
    ExchangeListRecord,
    IdentityBatchOutcome,
    SourceResult,
    UniverseIdentity,
    UniverseRequest,
    prepare_universe_request,
)

MAX_PDF_BYTES = 4 * 1024 * 1024
MAX_PDF_PAGES = 40
MAX_TEXT_BYTES = 262_144


class _ProofFailure(Exception):
    def __init__(self, code: str) -> None:
        super().__init__("The BSE material does not establish the required fact.")
        self.code = code


def _material_group(url: str) -> str:
    if type(url) is not str or len(url) > 1024:
        raise ValueError("The material needs an exact public PDF locator.")
    try:
        part = urlsplit(url)
        port = part.port
    except ValueError:
        raise ValueError("The material needs an exact public PDF locator.") from None
    if (
        part.scheme != "https"
        or part.username
        or part.password
        or port is not None
        or part.query
        or part.fragment
        or re.search(r"[\x00-\x20%\\]", url)
    ):
        raise ValueError("The material needs an exact public PDF locator.")
    if part.hostname == "static.cninfo.com.cn" and re.fullmatch(
        r"/finalpage/[0-9]{4}-[0-9]{2}-[0-9]{2}/[0-9]+\.pdf", part.path, re.I
    ):
        return "cninfo"
    if part.hostname == "www.bse.cn" and re.fullmatch(
        r"/disclosure/[0-9]{4}/[0-9]{4}-[0-9]{2}-[0-9]{2}/[a-zA-Z0-9-]+\.pdf",
        part.path,
        re.I,
    ):
        return "bse"
    raise ValueError("The material needs an exact public PDF locator.")


def _safe_issuer(value: str) -> bool:
    return (
        type(value) is str
        and 0 < len(value) <= 200
        and not any(category(c).startswith("C") or c in "/\\<>" for c in value)
    )


@dataclass(frozen=True, slots=True, weakref_slot=True, eq=False, init=False)
class BseObservedMaterial:
    """A producer-issued version/availability observation, without raw payload."""

    url: str
    body_sha256: str
    available_upper_bound: str
    lane_result: ProviderLaneResult[None]

    def __init__(self) -> None:
        raise TypeError("Observe the real material through its source owner.")


@dataclass(frozen=True, slots=True)
class _ObservedVersion:
    url: str
    body_sha256: str
    available_upper_bound: str
    lane_result: ProviderLaneResult[None]


_OBSERVATION_LOCK = Lock()
_OBSERVATION_ORIGINS: WeakKeyDictionary[BseObservedMaterial, _ObservedVersion] = (
    WeakKeyDictionary()
)


def _observed_version(material: BseObservedMaterial) -> _ObservedVersion:
    if type(material) is not BseObservedMaterial:
        raise TypeError("Use producer-issued material observations.")
    with _OBSERVATION_LOCK:
        version = _OBSERVATION_ORIGINS.get(material)
    if version is None or (
        material.url,
        material.body_sha256,
        material.available_upper_bound,
        material.lane_result,
    ) != (
        version.url,
        version.body_sha256,
        version.available_upper_bound,
        version.lane_result,
    ):
        raise ValueError(
            "The material observation is not producer-issued and unchanged."
        )
    return version


@dataclass(frozen=True, slots=True)
class BseMembershipEventTarget:
    source_code: str
    issuer_name: str
    old_share: BseObservedMaterial
    new_share: BseObservedMaterial
    listing: BseObservedMaterial

    def __post_init__(self) -> None:
        if (
            type(self.source_code) is not str
            or re.fullmatch(r"[0-9]{6}", self.source_code) is None
            or not _safe_issuer(self.issuer_name)
        ):
            raise ValueError("The BSE target needs a source code and exact issuer.")
        for material in (self.old_share, self.new_share, self.listing):
            _material_group(_observed_version(material).url)


def _parse_material_pages(
    role: str, code: str, issuer: str, pages: list[str]
) -> dict[str, str]:
    compact = [re.sub(r"\s+", "", p) for p in pages]
    if (
        not compact
        or len(compact) > MAX_PDF_PAGES
        or sum(len(p.encode()) for p in pages) > MAX_TEXT_BYTES
        or issuer not in "".join(compact)
    ):
        raise _ProofFailure("material_identity_unproved")
    matching = [
        p
        for p in compact
        if re.search(r"(?:股票|证券)代码(?:为|[:：])?[“\"]?" + code + r"(?![0-9])", p)
    ]
    if not matching:
        raise _ProofFailure("material_identity_unproved")
    if role == "old_share":
        for page in matching:
            stock_codes = set(
                re.findall(r"股票代码(?:为|[:：])?[“\"]?([0-9]{6})(?![0-9])", page)
            )
            if stock_codes and stock_codes != {code}:
                continue
            for match in re.finditer("人民币普通股", page):
                context = page[max(0, match.start() - 80) : match.end() + 120]
                if re.search(r"北京证券交易所(?:证券)?上市", context) and not re.search(
                    r"(?:拟|未来|将于).{0,12}(?:发行|上市)", context
                ):
                    return {"role": role, "code": code, "security_category": "A_SHARE"}
    elif role == "new_share":
        if any(
            "权益变动" in p
            and re.search(r"股票种类[:：]?人民币普通股", p)
            and set(re.findall(r"股票代码(?:为|[:：])?[“\"]?([0-9]{6})(?![0-9])", p))
            == {code}
            for p in matching
        ):
            return {"role": role, "code": code, "security_category": "A_SHARE"}
    elif role == "listing":
        dates: set[str] = set()
        for page in matching:
            for match in re.finditer(
                r"北京证券交易所.{0,20}?上市(?:时间|日期)[:：]?([0-9]{4})年([0-9]{1,2})月([0-9]{1,2})日",
                page,
            ):
                try:
                    dates.add(
                        datetime(int(match[1]), int(match[2]), int(match[3]))
                        .date()
                        .isoformat()
                    )
                except ValueError:
                    pass
        if len(dates) == 1:
            return {"role": role, "code": code, "bse_listing_date": dates.pop()}
    raise _ProofFailure("material_role_unproved")


def _decode_material(
    payload: bytes, role: str, code: str, issuer: str, timeout: float
) -> dict[str, str]:
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                str(Path(__file__).with_name("bse_membership_pdf_worker.py")),
                role,
                code,
                issuer,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        output, _ = process.communicate(payload, timeout=max(0.001, timeout))
    except subprocess.TimeoutExpired:
        raise _ProofFailure("material_pdf_timeout") from None
    except OSError:
        raise _ProofFailure("material_pdf_unavailable") from None
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait()
            if process.stdin is not None:
                process.stdin.close()
            if process.stdout is not None:
                process.stdout.close()
    if process.returncode != 0 or len(output) > 4096:
        raise _ProofFailure("material_pdf_unavailable")
    try:
        value: object = json.loads(output)
    except (ValueError, UnicodeError):
        raise _ProofFailure("material_pdf_unavailable") from None
    if type(value) is not dict or any(type(v) is not str for v in value.values()):
        raise _ProofFailure("material_pdf_unavailable")
    fields = cast(dict[str, str], value)
    if fields.get("failure") in {
        "material_identity_unproved",
        "material_role_unproved",
    }:
        raise _ProofFailure(fields["failure"])
    expected = {
        "role",
        "code",
        "bse_listing_date" if role == "listing" else "security_category",
    }
    if (
        set(fields) != expected
        or fields.get("role") != role
        or fields.get("code") != code
        or (role != "listing" and fields.get("security_category") != "A_SHARE")
    ):
        raise _ProofFailure("material_pdf_unavailable")
    return fields


class _MaterialRead:
    def __init__(
        self,
        url: str,
        transport: HttpTransport | None,
        timeout: float,
        *,
        witness: _ObservedVersion | None = None,
        role: str = "",
        code: str = "",
        issuer: str = "",
    ) -> None:
        self.url, self.transport, self.timeout = url, transport, timeout
        self.witness, self.role, self.code, self.issuer = witness, role, code, issuer
        self.lock = Lock()
        self.value: tuple[str, str, dict[str, str]] | None = None
        self.failure: str | None = None

    @staticmethod
    def _get(
        transport: HttpTransport, url: str, headers: dict[str, str]
    ) -> HttpResponse:
        response = transport.get(url, headers)
        if response.status == 429:
            raise TransportError("rate_limited", "The source request was rate limited.")
        return response

    def execute(self, invocation: ProviderInvocation) -> None:
        start = monotonic()
        try:
            headers = {
                "Accept": "application/pdf",
                "User-Agent": "a-share-research-skill/1",
            }
            response = (
                UrlLibTransport(provider_invocation=invocation).get_with_timeout(
                    self.url, headers, self.timeout
                )
                if self.transport is None
                else invocation.call(
                    ProductionInvocationAdapter(
                        partial(self._get, self.transport, self.url, headers)
                    )
                )
            )
            if response.status in {401, 403}:
                raise _ProofFailure("source_access_blocked")
            if (
                response.status != 200
                or response.content_type.split(";", 1)[0].lower().strip()
                not in {"application/pdf", "application/octet-stream"}
                or not 0 < len(response.body) <= MAX_PDF_BYTES
                or not response.body.startswith(b"%PDF-")
            ):
                raise _ProofFailure("material_response_unproved")
            if (
                response.retrieved_at.tzinfo is None
                or response.retrieved_at.utcoffset() is None
            ):
                raise _ProofFailure("material_retrieval_unproved")
            digest = hashlib.sha256(response.body).hexdigest()
            stamp = response.retrieved_at.isoformat()
            fields: dict[str, str] = {}
            if self.witness is not None:
                if (
                    digest != self.witness.body_sha256
                    or datetime.fromisoformat(self.witness.available_upper_bound)
                    > response.retrieved_at
                ):
                    raise _ProofFailure("material_version_changed")
                fields = _decode_material(
                    response.body,
                    self.role,
                    self.code,
                    self.issuer,
                    self.timeout - (monotonic() - start),
                )
            with self.lock:
                self.value = digest, stamp, fields
        except (_ProofFailure, TransportError) as error:
            with self.lock:
                self.failure = (
                    error.code
                    if isinstance(error, _ProofFailure)
                    else "source_access_blocked"
                    if is_permission_denied(error)
                    else "material_transport_unavailable"
                )
            raise

    def snapshot(self) -> tuple[tuple[str, str, dict[str, str]] | None, str | None]:
        with self.lock:
            return self.value, self.failure


@dataclass(frozen=True, slots=True)
class BseMaterialObservation:
    source_result: SourceResult
    material: BseObservedMaterial | None
    limitation_codes: tuple[str, ...]
    lane_result: ProviderLaneResult[None]

    def to_public_dict(self) -> dict[str, object]:
        return {
            "operation_id": "bse_material_observation@1",
            "source_result": self.source_result,
            "attempted": bool(self.lane_result.attempts),
            "material": None
            if self.material is None
            else {
                "version_ref": "bse-material:" + self.material.body_sha256,
                "available_upper_bound": self.material.available_upper_bound,
                "availability_role": "observed_retrieval_upper_bound_only",
            },
            "limitation_codes": list(self.limitation_codes),
            "attempts": [
                {
                    "provider_group": a.provider_group,
                    "sequence": a.sequence,
                    "state": a.state,
                }
                for a in self.lane_result.attempts
            ],
            "quiescence": self.lane_result.quiescence.state,
        }


def observe_bse_material(
    url: str,
    *,
    transport: HttpTransport | None,
    lanes: ProviderLanes,
    policy: ProviderLanePolicy,
) -> BseMaterialObservation:
    """Prepare source-version metadata for a later, independently frozen request.

    No research cutoff is advanced here. A new version first observed after an
    existing request's cutoff cannot become eligible in that request.
    """
    group = _material_group(url)
    if isinstance(transport, UrlLibTransport):
        raise TypeError("The document owner must own its counted urllib transport.")
    source = _MaterialRead(
        url, transport, policy.call_timeout_seconds or policy.timeout_seconds
    )
    lane = lanes.run(provider_group=group, adapter=source, policy=policy)
    value, failure = source.snapshot()
    if value is None or lane.failure_code is not None:
        codes = tuple(c for c in (failure, lane.failure_code) if c is not None)
        return BseMaterialObservation(
            "credential_blocked"
            if failure == "source_access_blocked"
            else "unavailable",
            None,
            codes,
            lane,
        )
    witness = object.__new__(BseObservedMaterial)
    for field, data in (
        ("url", url),
        ("body_sha256", value[0]),
        ("available_upper_bound", value[1]),
        ("lane_result", lane),
    ):
        object.__setattr__(witness, field, data)
    with _OBSERVATION_LOCK:
        _OBSERVATION_ORIGINS[witness] = _ObservedVersion(url, value[0], value[1], lane)
    return BseMaterialObservation("complete", witness, (), lane)


@dataclass(frozen=True, slots=True)
class BseMembershipEventAcquisition:
    membership_effective_date: str
    source_result: SourceResult
    records: tuple[ExchangeListRecord, ...]
    limitation_codes: tuple[str, ...]
    lane_results: tuple[ProviderLaneResult[None], ...]
    pages_received: int
    code_event: BseCodeMappingRecord | None
    bse_listing_date: str | None
    material_versions: tuple[_ObservedVersion, ...]

    def to_public_dict(self) -> dict[str, object]:
        return {
            "operation_id": "bse_code_cutover_membership@1",
            "code_event": None
            if self.code_event is None
            else self.code_event.to_public_dict(),
            "bse_listing_date": self.bse_listing_date,
            "material_versions": [
                {
                    "role": role,
                    "source_uri": version.url,
                    "body_sha256": version.body_sha256,
                    "available_upper_bound": version.available_upper_bound,
                    "availability_role": "observed_retrieval_upper_bound_only",
                    "observation_attempts": [
                        {
                            "provider_group": a.provider_group,
                            "sequence": a.sequence,
                            "state": a.state,
                        }
                        for a in version.lane_result.attempts
                    ],
                }
                for role, version in zip(
                    ("old_share", "new_share", "listing"),
                    self.material_versions,
                    strict=True,
                )
            ],
            "membership_effective_date": self.membership_effective_date,
            "source_result": self.source_result,
            "expected_count": None,
            "pages_expected": None,
            "pages_received": self.pages_received,
            "attempted": any(r.attempts for r in self.lane_results),
            "records": [
                {
                    "exchange": r.exchange,
                    "code": r.code,
                    "name": r.name,
                    "security_category": r.security_category,
                    "effective_at": r.effective_at,
                    "available_at": r.available_at,
                    "retrieved_at": r.retrieved_at,
                    "provenance_uri": r.provenance_uri,
                }
                for r in self.records
            ],
            "limitation_codes": list(self.limitation_codes),
            "attempts": [
                {
                    "stage": stage,
                    "provider_group": a.provider_group,
                    "sequence": a.sequence,
                    "state": a.state,
                }
                for stage, r in enumerate(self.lane_results, 1)
                for a in r.attempts
            ],
        }


class _RequestView(Protocol):
    screen_date: str
    information_cutoff: str


class BseMembershipEventAdapter:
    exchange = "BSE"
    operation_id = "bse_code_cutover_membership@1"

    def __init__(
        self,
        *,
        target: BseMembershipEventTarget,
        research_now: datetime,
        lanes: ProviderLanes,
        policy: ProviderLanePolicy,
        material_transport: HttpTransport | None,
        mapping_transport: HttpTransport | None,
    ) -> None:
        if type(target) is not BseMembershipEventTarget:
            raise TypeError("The BSE event needs a producer-qualified source target.")
        if any(
            isinstance(t, UrlLibTransport)
            for t in (material_transport, mapping_transport)
        ):
            raise TypeError("The source must own its counted urllib transports.")
        for material in (target.old_share, target.new_share, target.listing):
            if not lanes.is_authoritative(_observed_version(material).lane_result):
                raise ValueError(
                    "Material observations must have authoritative source evidence."
                )
        self.code, self.issuer = target.source_code, target.issuer_name
        self.observations = tuple(
            _observed_version(m)
            for m in (target.old_share, target.new_share, target.listing)
        )
        self.now, self.lanes, self.policy = research_now, lanes, policy
        self.material_transport, self.mapping_transport = (
            material_transport,
            mapping_transport,
        )
        self.last_acquisition: BseMembershipEventAcquisition | None = None

    def acquire(self, request: object, timeout_seconds: float) -> ExchangeListOutcome:
        view = cast(_RequestView, request)
        prepared = prepare_universe_request(
            UniverseRequest(view.screen_date, view.information_cutoff),
            research_now=self.now,
        )
        results: list[ProviderLaneResult[None]] = []
        records: tuple[ExchangeListRecord, ...] = ()
        limitations = [
            "target_event_not_full_bse",
            "other_membership_dates_unproved",
            "current_state_unproved",
            "material_availability_is_observation_upper_bound",
        ]
        pages = 0
        code_event: BseCodeMappingRecord | None = None
        bse_listing_date: str | None = None
        start = monotonic()
        ceiling = min(timeout_seconds, self.policy.timeout_seconds)
        state: SourceResult = "unavailable"
        observations = self.observations
        try:
            if prepared.screen_date != CODE_CHANGE_EFFECTIVE_DATE:
                raise _ProofFailure("outside_proven_code_cutover_day")
            if any(
                datetime.fromisoformat(o.available_upper_bound)
                > datetime.fromisoformat(prepared.information_cutoff)
                for o in observations
            ):
                raise _ProofFailure("material_observation_after_cutoff")

            def remaining_policy() -> ProviderLanePolicy:
                budget = self.policy.actual_attempt_budget - sum(
                    len(r.attempts) for r in results
                )
                remaining = ceiling - (monotonic() - start)
                if budget < 1:
                    raise _ProofFailure("actual_attempt_budget_exhausted")
                if remaining <= 0:
                    raise _ProofFailure("source_timeout")
                return replace(
                    self.policy,
                    actual_attempt_budget=budget,
                    timeout_seconds=remaining,
                    call_timeout_seconds=min(
                        self.policy.call_timeout_seconds or remaining, remaining
                    ),
                )

            mapping = acquire_bse_code_mapping(
                UniverseRequest(prepared.screen_date, prepared.information_cutoff),
                research_now=self.now,
                transport=self.mapping_transport,
                lanes=self.lanes,
                policy=remaining_policy(),
            )
            results.append(mapping.lane_result)
            pages = int(mapping.observed_pair_count is not None)
            state = (
                mapping.source_result
                if mapping.source_result == "credential_blocked"
                else "partial"
                if pages
                else "unavailable"
            )
            limitations.extend(mapping.limitation_codes)
            pairs = [r for r in mapping.records if r.new_code == self.code]
            if (
                not mapping.cohort_complete
                or len(pairs) != 1
                or pairs[0].code_change_effective_date is None
                or pairs[0].available_at is None
            ):
                raise _ProofFailure("code_event_target_unproved")
            pair = pairs[0]
            code_event = pair
            cutover_day = pair.code_change_effective_date
            event_available = pair.available_at
            assert cutover_day is not None and event_available is not None
            retrieved = [pair.retrieved_at]
            facts = []
            for role, material, code in zip(
                ("old_share", "new_share", "listing"),
                observations,
                (pair.old_code, pair.new_code, pair.new_code),
                strict=True,
            ):
                policy = remaining_policy()
                source = _MaterialRead(
                    material.url,
                    self.material_transport,
                    policy.call_timeout_seconds or policy.timeout_seconds,
                    witness=material,
                    role=role,
                    code=code,
                    issuer=self.issuer,
                )
                lane = self.lanes.run(
                    provider_group=_material_group(material.url),
                    adapter=source,
                    policy=policy,
                )
                results.append(lane)
                value, failure = source.snapshot()
                if value is None or lane.failure_code is not None:
                    raise _ProofFailure(failure or "material_source_unavailable")
                retrieved.append(value[1])
                facts.append(value[2])
            bse_listing_date = facts[2]["bse_listing_date"]
            if bse_listing_date > prepared.screen_date:
                raise _ProofFailure("bse_admission_after_code_event")
            available = max(
                (event_available, *(o.available_upper_bound for o in observations)),
                key=datetime.fromisoformat,
            )
            if datetime.fromisoformat(available) > datetime.fromisoformat(
                prepared.information_cutoff
            ):
                raise _ProofFailure("material_observation_after_cutoff")
            reference = hashlib.sha256(
                "".join(o.body_sha256 for o in observations).encode()
            ).hexdigest()[:24]
            records = (
                ExchangeListRecord(
                    "BSE",
                    pair.new_code,
                    pair.name,
                    "A_SHARE",
                    cutover_day,
                    available,
                    max(retrieved, key=datetime.fromisoformat),
                    f"bse-cutover:{pair.old_code}:{pair.new_code}:{reference}",
                ),
            )
            state = "partial"
        except _ProofFailure as error:
            limitations.append(error.code)
            if error.code == "source_access_blocked":
                state = "credential_blocked"
        self.last_acquisition = BseMembershipEventAcquisition(
            prepared.screen_date,
            state,
            records,
            tuple(limitations),
            tuple(results),
            pages,
            code_event,
            bse_listing_date,
            observations,
        )
        return ExchangeListOutcome(
            self.operation_id,
            "BSE",
            prepared.screen_date,
            any(r.attempts for r in results),
            state,
            None,
            None,
            pages,
            records,
        )


class _ListingView(Protocol):
    exchange: str
    code: str
    name: str
    security_category: str


class BseMembershipIdentityAdapter:
    """Resolve an explicit prepared batch using the same finite event owner.

    The known batch denominator does not assert the exchange denominator. The
    event establishes state only on its cutover day; an unknown effective end
    never becomes a fabricated delisting date or a current-state assertion.
    """

    exchange = "BSE"
    operation_id = "bse_code_cutover_identity@1"

    def __init__(self, source_owner: BseMembershipEventAdapter) -> None:
        self._source_owner = source_owner

    def resolve(
        self, request: object, listings: tuple[object, ...], timeout_seconds: float
    ) -> IdentityBatchOutcome:
        expected: dict[str, str] = {}
        for listing in listings:
            view = cast(_ListingView, listing)
            exchange, code, name, security_category = (
                view.exchange,
                view.code,
                view.name,
                view.security_category,
            )
            if exchange != "BSE" or security_category != "A_SHARE" or code in expected:
                raise ValueError(
                    "The identity request must use unique prepared BSE A-share records."
                )
            expected[code] = name
        if not expected:
            return IdentityBatchOutcome(
                self.operation_id, "BSE", False, "unavailable", 0, ()
            )
        acquisition = self._source_owner.acquire(request, timeout_seconds)
        identities = tuple(
            UniverseIdentity(
                "BSE",
                row.code,
                row.name,
                row.security_category,
                "listed",
                row.effective_at,
                None,
                row.available_at,
                row.available_at,
                row.retrieved_at,
                row.provenance_uri,
            )
            for row in acquisition.records
            if expected.get(row.code) == row.name
        )
        state: SourceResult = "unavailable"
        if identities:
            state = "complete" if len(identities) == len(expected) else "partial"
        elif acquisition.source_result == "credential_blocked":
            state = "credential_blocked"
        return IdentityBatchOutcome(
            self.operation_id,
            "BSE",
            acquisition.attempted,
            state,
            len(expected),
            identities,
        )
