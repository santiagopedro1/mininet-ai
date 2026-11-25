"""Synthetic configuration → evidence → reported draft, never scientific selection."""

import copy
import json
import subprocess
import sys

import pytest

from examples.noms27.recovery_campaign.blocks import build_manifest


def fixture(manifest=None):
    from examples.noms27.recovery_campaign.calibration import preregister

    registration = preregister(manifest or build_manifest())
    records = []
    for number, block in enumerate(registration["calibration_blocks"]):
        for candidate in ["combined_ungated", *registration["candidates"]]:
            records.append(
                {
                    "trial_id": f"trial-{number}-{candidate}",
                    "block_id": f"block-{number}",
                    "replaces": None,
                    "registration_sha256": registration["registration_sha256"],
                    "evidence_kind": "synthetic_calibration_fixture",
                    "placement_id": block["placement_id"],
                    "repetition": block["repetition"],
                    "traffic_seed": block["traffic_seed"],
                    "environment_seed": block["environment_seed"],
                    "scenario_sha256": block["scenario_sha256"],
                    "candidate": candidate,
                    "configuration_sha256": registration["configuration_sha256"][
                        candidate
                    ],
                    "valid": True,
                    "invalid_reasons": [],
                    "recovery_failure": False,
                    "disruption_seconds": 10.0,
                }
            )
    return registration, records


def test_preregistration_preserves_candidates_allocation_and_pending_gates():
    from examples.noms27.recovery_campaign.calibration import preregister

    result = preregister(build_manifest())
    assert list(result["candidates"]) == ["gate_disabled"] + [
        f"threshold_{i / 10:.1f}" for i in range(11)
    ]
    assert result["allocation"]["primary_trials"] == 1440
    assert result["allocation"]["higher_load_trials"] == 1080
    assert len(result["calibration_blocks"]) == 180
    assert result["calibration_trial_count"] == 2340
    assert result["execution_authorized"] is False
    assert "Pedro_budget_resource_approval_pending" in result["blockers"]
    assert "models" in result["missing_metadata"]


def test_failure_count_eligibility_retains_failures_and_rejects_better_ineligible():
    from examples.noms27.recovery_campaign.calibration import report

    registration, records = fixture()
    for row in records:
        if row["candidate"] == "threshold_0.3":
            row["disruption_seconds"] = 1.0
            row["recovery_failure"] = row["trial_id"] == "trial-0-threshold_0.3"
        if row["candidate"] == "threshold_0.7":
            row["disruption_seconds"] = 8.0
    result = report(registration, records)
    assert result["selection"]["candidate"] == "threshold_0.7"
    assert result["candidates"]["threshold_0.3"]["eligible"] is False
    assert result["candidates"]["threshold_0.3"]["failure_count"] == 1
    assert (
        result["candidates"]["threshold_0.3"]["class_failure_differences"][
            "diamond_link"
        ]
        == 1
    )
    assert result["candidates"]["threshold_0.7"]["mean_disruption_seconds"] == 8.0
    assert result["retained_records"] == records
    assert result["scientific_freeze"] is False
    assert result["execution_authorized"] is False
    assert "freeze_metadata_incomplete" in result["blockers"]
    assert result["draft_evaluation"]["selected_policy"] == {
        "enabled": True,
        "threshold": 0.7,
    }


def test_six_classes_have_equal_weight_not_equal_trial_weight():
    from examples.noms27.recovery_campaign.calibration import report
    from examples.noms27.recovery_campaign.scenarios import make_scenario

    manifest = build_manifest()
    extra = make_scenario("diamond_link", 3).model_copy(update={"split": "calibration"})
    manifest = manifest.model_copy(update={"scenarios": (*manifest.scenarios, extra)})
    registration, records = fixture(manifest)
    for row in records:
        if row["candidate"] == "threshold_0.5":
            row["disruption_seconds"] = (
                16.0 if row["placement_id"].startswith("diamond_link-") else 8.0
            )
    result = report(registration, records)
    # Class means 16, 8, 8, 8, 8, 8 → 9 1/3; pooled trials would be 10 2/7.
    assert result["selection"]["candidate"] == "threshold_0.5"
    assert result["candidates"]["threshold_0.5"][
        "mean_disruption_seconds"
    ] == pytest.approx(28 / 3)


@pytest.mark.parametrize("disruption", [10.0, 11.0])
def test_ties_or_no_improvement_keep_disabled_four_slots_and_negative_findings(
    disruption,
):
    from examples.noms27.recovery_campaign.calibration import report

    registration, records = fixture()
    for row in records:
        if row["candidate"].startswith("threshold_"):
            row["disruption_seconds"] = disruption
    result = report(registration, records)
    assert result["selection"]["candidate"] == "gate_disabled"
    assert len(result["candidates"]) == 13
    assert result["candidates"]["threshold_1.0"][
        "mean_disruption_seconds"
    ] == pytest.approx(disruption)
    draft = result["draft_evaluation"]
    assert draft["selected_policy"] == {"enabled": False, "threshold": None}
    assert draft["allocation"]["primary_trials"] == 1440
    assert draft["allocation"]["higher_load_trials"] == 1080
    assert {len(b["treatments"]) for b in draft["primary"]} == {4}
    assert {tuple(b["treatments"]) for b in draft["higher_load"]} == {
        ("s2_only", "combined_ungated", "combined_gated")
    }


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "duplicate",
        "heldout_seed",
        "heldout_placement",
        "wrong_environment",
        "scenario",
        "repetition",
        "candidate",
        "registration",
        "real",
        "bool_failure",
        "string_disruption",
        "negative",
        "extra",
        "unmatched",
    ],
)
def test_bad_or_unmatched_evidence_fails_closed_without_discarding_originals(damage):
    from examples.noms27.recovery_campaign.calibration import report

    registration, records = fixture()
    if damage == "missing":
        records.pop()
    elif damage == "duplicate":
        records.append(copy.deepcopy(records[0]))
    else:
        field, value = {
            "heldout_seed": ("traffic_seed", 2000),
            "heldout_placement": ("placement_id", "diamond_link-p1"),
            "wrong_environment": ("environment_seed", 99),
            "scenario": ("scenario_sha256", "forged"),
            "repetition": ("repetition", True),
            "candidate": ("candidate", "threshold_0.55"),
            "registration": ("registration_sha256", "later-tuning"),
            "real": ("evidence_kind", "heldout"),
            "bool_failure": ("recovery_failure", 1),
            "string_disruption": ("disruption_seconds", "1"),
            "negative": ("disruption_seconds", -1.0),
            "extra": ("approval", True),
            "unmatched": ("block_id", "unpaired"),
        }[damage]
        records[0][field] = value
    result = report(registration, records)
    assert result["selection"] is None
    assert result["errors"]
    assert result["retained_records"] == records
    assert result["draft_evaluation"]["selected_policy"] is None
    assert result["execution_authorized"] is False


def test_invalid_originals_require_explicit_complete_whole_block_replacement():
    from examples.noms27.recovery_campaign.calibration import report

    registration, records = fixture()
    originals = copy.deepcopy(records[:13])
    records[0]["valid"] = False
    records[0]["invalid_reasons"] = ["demonstrated_injection_failure"]
    incomplete = report(registration, records)
    assert incomplete["selection"] is None
    for row in originals:
        row["trial_id"] = "replacement-" + row["trial_id"]
        row["block_id"] = "replacement-block"
        row["replaces"] = "block-0"
    records.extend(originals)
    result = report(registration, records)
    assert result["selection"]["candidate"] == "gate_disabled"
    assert result["retained_records"] == records
    assert result["excluded_blocks"] == [
        {
            "block_id": "block-0",
            "replaced_by": "replacement-block",
            "reasons": ["demonstrated_injection_failure"],
        }
    ]
    assert result["matched_block_count"] == 180


def test_an_ineligible_disabled_fixture_cannot_hide_an_eligible_improvement():
    from examples.noms27.recovery_campaign.calibration import report

    registration, records = fixture()
    for row in records:
        if row["candidate"] == "gate_disabled":
            row["disruption_seconds"] = 1.0
            row["recovery_failure"] = True
        elif row["candidate"] == "threshold_0.2":
            row["disruption_seconds"] = 8.0
    result = report(registration, records)
    assert result["selection"]["candidate"] == "threshold_0.2"


def test_candidate_configuration_must_bind_policy_and_shared_s1_interface():
    from examples.noms27.recovery_campaign.calibration import report

    registration, records = fixture()
    # A record cannot substitute a different threshold or ungated policy.
    records[2]["configuration_sha256"] = registration["configuration_sha256"][
        "gate_disabled"
    ]
    result = report(registration, records)
    assert result["selection"] is None
    assert "configuration" in str(result["errors"])


def test_partial_freeze_metadata_is_preserved_but_reports_missing_details():
    from examples.noms27.recovery_campaign.calibration import preregister, report

    registration = preregister(
        build_manifest(),
        {"models": {"s1": "fixture"}, "code_revision": "synthetic-revision"},
    )
    result = report(registration, [])
    assert registration["metadata"]["models"]["s1"] == "fixture"
    assert "models.s2" in registration["missing_metadata"]
    assert "code_revision" not in registration["missing_metadata"]
    assert result["draft_evaluation"]["metadata"] == registration["metadata"]
    assert result["draft_evaluation"]["approvals"] == {
        "budget_resource": None,
        "frozen_evaluation_Pedro": None,
    }
    assert result["scientific_freeze"] is False
    assert result["execution_authorized"] is False


@pytest.mark.parametrize(
    "metadata",
    [
        {"models": True},
        {"budgets": "approved"},
        {"code_revision": 1},
        {"execution_authorized": True},
    ],
)
def test_malformed_freeze_metadata_or_approval_fields_are_rejected(metadata):
    from examples.noms27.recovery_campaign.calibration import preregister

    with pytest.raises(ValueError):
        preregister(build_manifest(), metadata)


def test_cli_preregisters_then_reports_synthetic_evidence_without_overwriting(tmp_path):
    from examples.noms27.recovery_campaign.calibration import preregister

    command = [sys.executable, "-m", "examples.noms27.recovery_campaign.calibration"]
    registration_path = tmp_path / "registration.json"
    created = subprocess.run(
        [*command, "preregister", "--output", str(registration_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert created.returncode == 0, created.stderr
    assert json.loads(registration_path.read_text()) == preregister(build_manifest())
    registration, records = fixture()
    evidence = tmp_path / "synthetic-evidence.json"
    evidence.write_text(json.dumps(records))
    output = tmp_path / "draft-report.json"
    args = [
        *command,
        "report",
        "--registration",
        str(registration_path),
        "--evidence",
        str(evidence),
        "--output",
        str(output),
    ]
    completed = subprocess.run(args, capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(output.read_text())
    assert result["selection"]["candidate"] == "gate_disabled"
    assert result["sources"]["evidence"]["sha256"]
    before = output.read_bytes()
    assert subprocess.run(args, capture_output=True, check=False).returncode != 0
    assert output.read_bytes() == before
    assert json.loads(evidence.read_text()) == records
    assert json.loads(registration_path.read_text()) == registration
    assert (
        subprocess.run(
            [
                *command,
                "preregister",
                "--approve",
                "--output",
                str(tmp_path / "forbidden.json"),
            ],
            capture_output=True,
            check=False,
        ).returncode
        != 0
    )


def test_registration_mutation_and_boolean_threshold_tampering_are_rejected():
    from examples.noms27.recovery_campaign.calibration import report

    registration, records = fixture()
    registration["candidates"]["threshold_0.0"]["threshold"] = False
    with pytest.raises(ValueError, match="snapshot|hash"):
        report(registration, records)


def test_equal_total_failures_allow_adverse_class_difference_without_noninferiority_claim():
    from examples.noms27.recovery_campaign.calibration import report

    registration, records = fixture()
    for row in records:
        if row["candidate"] == "combined_ungated":
            row["recovery_failure"] = row["trial_id"] == "trial-0-combined_ungated"
        elif row["candidate"] == "threshold_0.6":
            row["recovery_failure"] = row["trial_id"] == "trial-30-threshold_0.6"
            row["disruption_seconds"] = 8.0
    result = report(registration, records)
    assert result["selection"]["candidate"] == "threshold_0.6"
    summary = result["candidates"]["threshold_0.6"]
    assert summary["eligible"] is True
    assert summary["class_failure_differences"]["diamond_link"] == -1
    assert summary["class_failure_differences"]["diamond_switch"] == 1
    assert summary["mean_disruption_difference_seconds"] == pytest.approx(-2.0)
    assert summary["class_sample_counts"]["diamond_link"] == 30
    assert "not population noninferiority" in result["interpretation"]


def test_draft_binds_selected_policy_to_fourth_slot_only_and_retains_freeze_snapshot():
    from examples.noms27.recovery_campaign.calibration import report

    registration, records = fixture()
    result = report(registration, records)
    draft = result["draft_evaluation"]
    assert draft["treatment_policies"] == {
        "s1_only": {"enabled": False, "threshold": None},
        "s2_only": {"enabled": False, "threshold": None},
        "combined_ungated": {"enabled": False, "threshold": None},
        "combined_gated": {"enabled": False, "threshold": None},
    }
    assert draft["registration_sha256"] == registration["registration_sha256"]
    assert draft["calibration_selection_sha256"]
    assert result["scientific_selected_policy"] is None
    assert draft["execution_authorized"] is False


@pytest.mark.parametrize(
    "metadata",
    [
        {"budgets": {"s1_cap_seconds": True}},
        {"budgets": {"common_deadline_seconds": -1}},
        {"budgets": {"s1_cap_seconds": 5, "common_deadline_seconds": 4}},
        {"workload": {"payload_bytes": 512}},
        {"workload": {"primary_load": "0.1"}},
        {"analysis": {"interval_level": 0.9}},
        {"analysis": {"within_placement": False}},
    ],
)
def test_conflicting_or_malformed_numerical_freeze_metadata_is_rejected(metadata):
    from examples.noms27.recovery_campaign.calibration import preregister

    with pytest.raises(ValueError):
        preregister(build_manifest(), metadata)


@pytest.mark.parametrize(
    "damage", ["overlap", "too_few", "bool_seed", "renamed_placement", "reduced_slots"]
)
def test_preregistration_rejects_contaminated_or_reduced_design(damage):
    from examples.noms27.recovery_campaign.calibration import preregister

    manifest = build_manifest()
    if damage == "overlap":
        manifest = manifest.model_copy(
            update={"evaluation_seeds": manifest.calibration_seeds}
        )
    elif damage == "too_few":
        manifest = manifest.model_copy(update={"repetitions": 29})
    elif damage == "bool_seed":
        manifest = manifest.model_copy(
            update={"calibration_seeds": (True, *manifest.calibration_seeds[1:])}
        )
    elif damage == "reduced_slots":
        manifest = manifest.model_copy(
            update={"primary_treatments": manifest.primary_treatments[:3]}
        )
    else:
        calibration = manifest.scenarios[0]
        forged = calibration.model_copy(
            update={"placement_id": "renamed-heldout", "split": "heldout"}
        )
        manifest = manifest.model_copy(
            update={"scenarios": (*manifest.scenarios, forged)}
        )
    with pytest.raises(ValueError):
        preregister(manifest)


@pytest.mark.parametrize(
    "damage",
    [
        "valid_replacement",
        "orphan",
        "cycle",
        "partial",
        "unknown_invalid_reason",
        "valid_with_reason",
    ],
)
def test_replacements_cannot_cherry_pick_valid_blocks_or_hide_malformed_originals(
    damage,
):
    from examples.noms27.recovery_campaign.calibration import report

    registration, records = fixture()
    replacement = copy.deepcopy(records[:13])
    for row in replacement:
        row["trial_id"] = "replacement-" + row["trial_id"]
        row["block_id"] = "replacement-block"
        row["replaces"] = "block-0"
    if damage != "valid_replacement":
        records[0]["valid"] = False
        records[0]["invalid_reasons"] = ["demonstrated_injection_failure"]
    if damage == "orphan":
        for row in replacement:
            row["replaces"] = "unknown"
    elif damage == "cycle":
        for row in records[:13]:
            row["replaces"] = "replacement-block"
    elif damage == "partial":
        replacement.pop()
    elif damage == "unknown_invalid_reason":
        records[0]["invalid_reasons"] = ["model_failure"]
    elif damage == "valid_with_reason":
        records[0]["valid"] = True
    records.extend(replacement)
    result = report(registration, records)
    assert result["selection"] is None
    assert result["retained_records"] == records


@pytest.mark.parametrize("text", ['{"x":1,"x":2}', "[NaN]", "[Infinity]", "[1e999]"])
def test_cli_malformed_json_never_emits_a_draft(tmp_path, text):
    from examples.noms27.recovery_campaign.calibration import preregister

    registration = tmp_path / "registration.json"
    registration.write_text(json.dumps(preregister(build_manifest())))
    evidence = tmp_path / "bad.json"
    evidence.write_text(text)
    output = tmp_path / "must-not-exist.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "examples.noms27.recovery_campaign.calibration",
            "report",
            "--registration",
            str(registration),
            "--evidence",
            str(evidence),
            "--output",
            str(output),
        ],
        capture_output=True,
        check=False,
    )
    assert completed.returncode != 0
    assert not output.exists()
    assert evidence.read_text() == text


def test_json_object_key_order_cannot_change_preregistered_numeric_tie_break():
    from examples.noms27.recovery_campaign.calibration import report

    registration, records = fixture()
    for row in records:
        if row["candidate"] in {"threshold_0.1", "threshold_0.9"}:
            row["disruption_seconds"] = 8.0
    registration["candidates"] = dict(
        reversed(list(registration["candidates"].items()))
    )
    assert report(registration, records)["selection"]["candidate"] == "threshold_0.1"
