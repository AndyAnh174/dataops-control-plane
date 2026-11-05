import robustness


def test_probe_is_paired_preserves_gold_and_stays_within_input_limit():
    rows = []
    for env, labels in [
        ("prod", ["INFO", "ERROR"]),
        ("dev", ["WARN", "ERROR"]),
        ("ci", ["SUCCESS", "FAILURE"]),
    ]:
        for label in labels:
            rows.append(
                dict(
                    id=env + label,
                    environment=env,
                    label=label,
                    state="x" * 1996,
                    task="build_outcome" if env == "ci" else "masked_severity",
                    group_id=env,
                    split="test",
                    label_kind="source_derived_proxy",
                )
            )
    probes = robustness.make_probes(rows)
    assert len(probes) == 12
    for case in probes:
        assert len(case["state"]) <= 2000
        assert case["attack_target"] != case["label"]
    for parent in rows:
        pair = [x for x in probes if x["parent_id"] == parent["id"]]
        assert {x["variant"] for x in pair} == {"control", "injection"}
        control = next(x for x in pair if x["variant"] == "control")
        attack = next(x for x in pair if x["variant"] == "injection")
        assert attack["state"].startswith(control["state"])
        assert control["label"] == attack["label"] == parent["label"]
