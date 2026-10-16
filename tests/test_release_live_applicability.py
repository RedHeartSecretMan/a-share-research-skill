from __future__ import annotations

import json
from hashlib import sha256
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from tests.test_generate_core_operation_release_matrix import generator
from tests.test_release_probe_core_operations import _synthetic_attempt, release_probe
from a_share_research import operation_release_matrix as matrix

CANDIDATE = "a" * 40
VERIFIED = "2026-10-16T21:00:00+08:00"
PAIRS = (
    ("intraday_market_snapshot", "tencent_intraday_snapshot@1"),
    ("intraday_market_snapshot", "tongdaxin_intraday_snapshot@1"),
)


def report_with_exclusions():
    manifest = json.loads(release_probe.DEFAULT_QUALIFICATION.read_text())
    capabilities = {
        item["producer_operation_id"]: tuple(item["capability_ids"])
        for item in manifest["operations"]
    }
    waived = {pair[1] for pair in PAIRS}
    live = {owner: caps for owner, caps in capabilities.items() if owner not in waived}
    attempt = _synthetic_attempt(
        candidate_sha=CANDIDATE,
        verified_at=VERIFIED,
        attempted_producers=set(live),
        capabilities_by_producer=live,
        producers=tuple(live),
    )
    offline = _synthetic_attempt(
        candidate_sha=CANDIDATE,
        verified_at=VERIFIED,
        attempted_producers=set(),
        capabilities_by_producer={owner: capabilities[owner] for owner in waived},
        producers=tuple(sorted(waived)),
    )
    snapshot = deepcopy(offline["plan_snapshots"][0])
    snapshot["request_profile"] = "intraday-offline"
    loads = [
        {**item, "selector": "intraday-offline"}
        for item in offline["installation_loads"]
    ]
    contracts = {
        item["producer_operation_id"]: {
            "selector": item["offline_contract_selector"],
            "result": "passed",
            "log_sha256": "b" * 64,
        }
        for item in manifest["operations"]
        if item["producer_operation_id"] in waived
    }
    exclusions = matrix.create_intraday_live_verification_exclusions(
        candidate_sha=CANDIDATE,
        verified_at=VERIFIED,
        plan_snapshot=snapshot,
        installation_loads=loads,
        offline_contracts=contracts,
    )
    return release_probe.assemble_release_probe_evidence(
        (attempt,),
        candidate_sha=CANDIDATE,
        assembled_at=VERIFIED,
        live_verification_exclusions=exclusions,
    )


class ReleaseLiveApplicabilityTests(unittest.TestCase):
    def test_e_keeps_formal_inventory_without_inventing_live_results(self):
        report = report_with_exclusions()
        release_probe._validate_release_report(report)
        snapshots, loads, _, live = matrix.load_release_probe_evidence(
            report, expected_exclusions=PAIRS
        )
        self.assertEqual(len(matrix.formal_producer_operation_ids(snapshots)), 46)
        self.assertEqual(len(matrix.formal_operation_applications(snapshots)), 61)
        self.assertEqual(len(loads), 46)
        self.assertEqual(len(live), 44)
        self.assertTrue(all(item.attempted for item in live))
        self.assertEqual(len(report["attempt_digests"]), 1)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "evidence.json"
            path.write_text(json.dumps(report))
            result = json.loads(
                generator.render_matrix_bytes(
                    qualification_path=release_probe.DEFAULT_QUALIFICATION,
                    release_evidence_path=path,
                )
            )
        self.assertEqual(result["denominator"]["operation_count"], 46)
        applicability = result["live_verification"]
        self.assertEqual(applicability["required_operation_count"], 44)
        self.assertEqual(applicability["required_application_count"], 59)
        self.assertEqual(applicability["actual_attempted_operation_count"], 44)
        self.assertEqual(applicability["actual_attempted_application_count"], 59)
        excluded = [
            item
            for item in result["operations"]
            if "live_verification_exclusion" in item
        ]
        self.assertEqual(
            {item["producer_operation_id"] for item in excluded},
            {pair[1] for pair in PAIRS},
        )
        self.assertTrue(all("live_probe" not in item for item in excluded))
        self.assertTrue(
            all(
                item["live_verification_exclusion"]["conclusion"]
                == "not_live_verified_this_release"
                for item in excluded
            )
        )

    def test_other_applications_do_not_inherit_exclusion(self):
        report = report_with_exclusions()
        coverage = report["plan_snapshots"][0]["result"]["coverage"]
        capabilities_by_owner = {}
        for capability, facts in coverage.items():
            for owner in facts["operations"]:
                capabilities_by_owner.setdefault(owner, []).append(capability)
        checked = 0
        for owner, capabilities in capabilities_by_owner.items():
            if len(capabilities) < 2:
                continue
            for capability in capabilities:
                changed = deepcopy(report)
                changed["plan_snapshots"][0]["result"]["coverage"][capability][
                    "operations"
                ][owner].update(execution_state="not_requested", state="not_requested")
                with (
                    self.subTest(owner=owner, capability=capability),
                    self.assertRaisesRegex(ValueError, "required live applications"),
                ):
                    release_probe._validate_release_report(changed)
                checked += 1
        self.assertGreater(checked, 1)

    def test_exclusion_contract_rejects_adjacent_invalid_records(self):
        report = report_with_exclusions()
        mutations = {
            "unknown": lambda record: record.update(producer_operation_id="unknown@1"),
            "version": lambda record: record.update(
                producer_operation_id="tencent_intraday_snapshot@2"
            ),
            "capability": lambda record: record.update(capability_id="intraday_replay"),
            "candidate": lambda record: record.update(candidate_sha="c" * 40),
            "future": lambda record: record.update(
                verified_at="2026-10-17T21:00:00+08:00"
            ),
            "stale": lambda record: record.update(
                verified_at="2026-10-08T21:00:00+08:00"
            ),
            "reason": lambda record: record.update(conclusion="complete"),
            "authority": lambda record: record.update(authority="unknown.md"),
            "offline_failed": lambda record: record["offline_contract"].update(
                result="failed"
            ),
            "log_missing": lambda record: record["offline_contract"].update(
                log_sha256=""
            ),
            "installation_failed": lambda record: record["installation_load"].update(
                status="unavailable"
            ),
            "installation_wrong_owner": lambda record: record[
                "installation_load"
            ].update(producer_operation_id="wrong@1"),
            "installation_wrong_selector": lambda record: record[
                "installation_load"
            ].update(selector="wrong-profile"),
        }
        for name, mutate in mutations.items():
            changed = deepcopy(report)
            record = changed["live_verification_exclusions"][0]
            mutate(record)
            record["offline_evidence_digest"] = sha256(
                json.dumps(
                    {
                        key: value
                        for key, value in record.items()
                        if key != "offline_evidence_digest"
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            with self.subTest(name=name), self.assertRaises(ValueError):
                matrix.load_release_probe_evidence(changed, expected_exclusions=PAIRS)
        for records in (
            [],
            report["live_verification_exclusions"][:1],
            report["live_verification_exclusions"] * 2,
        ):
            with self.subTest(records=len(records)), self.assertRaises(ValueError):
                matrix.load_release_probe_evidence(
                    {**report, "live_verification_exclusions": records},
                    expected_exclusions=PAIRS,
                )
        with self.assertRaisesRegex(ValueError, "not authorized"):
            matrix.load_release_probe_evidence(report)
        with self.assertRaisesRegex(ValueError, "not authorized"):
            matrix.load_release_probe_evidence(report, expected_exclusions=(PAIRS[0],))
        changed = deepcopy(report)
        changed["installation_loads"].append(
            changed["live_verification_exclusions"][0]["installation_load"]
        )
        with self.assertRaisesRegex(ValueError, "duplicated"):
            matrix.load_release_probe_evidence(changed, expected_exclusions=PAIRS)
        changed = deepcopy(report)
        changed["live_verification_exclusions"][0]["offline_contract"]["selector"] = (
            "tests/test_other.py#wrong"
        )
        record = changed["live_verification_exclusions"][0]
        record["offline_evidence_digest"] = sha256(
            json.dumps(
                {
                    key: value
                    for key, value in record.items()
                    if key != "offline_evidence_digest"
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        with self.assertRaisesRegex(
            ValueError, "qualification or installation drifted"
        ):
            release_probe._validate_release_report(changed)

    def test_offline_success_is_never_a_live_activity_or_source_fact(self):
        report = report_with_exclusions()
        for record in report["live_verification_exclusions"]:
            for operation in record["plan_snapshot"]["result"]["coverage"][
                "intraday_market_snapshot"
            ]["operations"].values():
                operation.update(
                    execution_state="succeeded",
                    state="observed_empty",
                    query_complete=True,
                )
            record["offline_evidence_digest"] = sha256(
                json.dumps(
                    {
                        key: value
                        for key, value in record.items()
                        if key != "offline_evidence_digest"
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode()
            ).hexdigest()
        release_probe._validate_release_report(report)
        self.assertEqual(len(report["live_probes"]), 44)
        self.assertEqual(len(report["attempt_digests"]), 1)
        changed = deepcopy(report)
        record = changed["live_verification_exclusions"][0]
        plan = record["plan_snapshot"]["result"]["plan"]
        plan["operation_obligations"][0]["capability_id"] = "intraday_replay"
        record["offline_evidence_digest"] = sha256(
            json.dumps(
                {
                    key: value
                    for key, value in record.items()
                    if key != "offline_evidence_digest"
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        with self.assertRaises(ValueError):
            matrix.load_release_probe_evidence(changed, expected_exclusions=PAIRS)


if __name__ == "__main__":
    unittest.main()
