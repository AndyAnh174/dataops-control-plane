import csv
import hashlib
import json
from importlib import import_module


def test_export_is_redacted_provenanced_and_does_not_change_raw_logs(tmp_path):
    source = tmp_path / "raw"
    source.mkdir()
    app = source / "app.log"
    app.write_text(
        '{"level":"ERROR","message":"database unavailable for alice@example.org"}\n',
        encoding="utf-8",
    )
    ci = source / "ci.log"
    ci.write_text(
        "compiler rejected module\npassword=secret-value\nFinished: FAILURE\n", encoding="utf-8"
    )
    prefix = source / "prefix.log"
    prefix.write_text("ERROR an incomplete record\n", encoding="utf-8")
    rows = []
    for path, environment, status in [
        (app, "prod", "copied"),
        (ci, "ci-shared", "copied"),
        (prefix, "dev", "copied_prefix"),
    ]:
        rows.append(
            {
                "id": path.stem,
                "environment": environment,
                "status": status,
                "local_path": path.name,
                "source_path": f"/logs/{path.name}",
                "source_server": "example.test",
                "source_changed_during_copy": "False",
                "sha256_local": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    manifest = tmp_path / "manifest.csv"
    with manifest.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    before = {path: path.read_bytes() for path in (app, ci, prefix)}
    report = import_module("prepare_dataset").prepare(
        manifest,
        source,
        tmp_path / "out",
        b"private-test-salt-not-for-production",
        max_per_class=20,
    )
    exported = (tmp_path / "out" / "cases.jsonl").read_text(encoding="utf-8")
    cases = [json.loads(line) for line in exported.splitlines()]
    assert {row["environment"] for row in cases} == {"prod", "ci"}
    assert {row["label"] for row in cases} == {"ERROR", "FAILURE"}
    assert "alice@" not in exported
    assert "secret-value" not in exported
    assert "database unavailable" in exported
    assert "compiler rejected" in exported
    assert report["excluded_files"]["copied_prefix"] == 1
    assert all(row["label_kind"] == "source_derived_proxy" for row in cases)
    assert all("source_path" not in row for row in cases)
    assert before == {path: path.read_bytes() for path in before}


def test_hash_mismatch_does_not_get_exported(tmp_path):
    source = tmp_path / "raw"
    source.mkdir()
    path = source / "app.log"
    path.write_text("ERROR changed after collection", encoding="utf-8")
    manifest = tmp_path / "manifest.csv"
    manifest.write_text(
        "id,environment,status,local_path,source_path,source_server,sha256_local\n"
        f"x,prod,copied,{path},/log/app.log,host,{'0' * 64}\n",
        encoding="utf-8",
    )
    report = import_module("prepare_dataset").prepare(
        manifest,
        source,
        tmp_path / "out",
        b"private-test-salt-not-for-production",
        max_per_class=20,
    )
    assert report["excluded_files"]["checksum_mismatch"] == 1
    assert (tmp_path / "out" / "cases.jsonl").read_text(encoding="utf-8") == ""
