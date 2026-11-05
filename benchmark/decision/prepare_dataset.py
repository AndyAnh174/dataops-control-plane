"""Read-only raw corpus preparation; exported labels are proxies, not incident truth."""

import argparse
import csv
import gzip
import hashlib
import hmac
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

from benchmark_core import (
    Redactor,
    ci_result,
    grouped_split,
    mask_ci_result,
    mask_severity,
    parse_event,
)

SEED = 2026
MAX_FILE_BYTES = 32 * 1024 * 1024


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fingerprint(text):
    text = re.sub(r"\[[A-Z_]+_[0-9a-f]{12}\]", "[REDACTED]", text)
    text = re.sub(r"\b\d{4}-\d\d-\d\d[^\s]*", "[TIME]", text)
    text = re.sub(r"\b\d+(?:\.\d+)?\b", "[N]", text)
    return hashlib.sha256(re.sub(r"\s+", " ", text).strip().encode()).hexdigest()


def source_group(row, salt):
    source = row.get("source_path", "")
    if "/pods/" in source or "/containers/" in source:
        source = str(Path(source).parent).replace("\\", "/")
    elif row["environment"] != "ci-shared":
        source = re.sub(r"(?:\.\d+)?(?:\.gz)?$", "", source)
    value = row["environment"] + row.get("source_server", "") + source
    return hmac.new(salt, value.encode(), hashlib.sha256).hexdigest()[:24]


def clip(text):
    return text if len(text) <= 2000 else text[:490] + "\n[OMITTED MIDDLE]\n" + text[-1490:]


def prepare(manifest, source_root, outdir, salt, max_per_class=60):
    manifest, source_root, outdir = Path(manifest), Path(source_root).resolve(), Path(outdir)
    if max_per_class < 1:
        raise ValueError("max_per_class must be positive")
    outdir.mkdir(parents=True, exist_ok=True)
    redactor = Redactor(salt)
    excluded, parse_counts = Counter(), Counter()
    pool, conflicts, linked = {}, set(), []
    total_read = included_files = 0

    def add(row, state, label, label_source, group):
        state = clip(redactor.redact(state)).strip()
        if not state:
            return
        env = "ci" if row["environment"] == "ci-shared" else row["environment"]
        task = "build_outcome" if env == "ci" else "masked_severity"
        fp = fingerprint(state)
        key = (env, task, fp)
        old = pool.get(key)
        if old:
            linked.append((old["group_id"], group))
            if old["label"] != label:
                conflicts.add(key)
            parse_counts["duplicate_templates"] += 1
            return
        if len(pool) >= 20000:
            parse_counts["candidate_pool_cap"] += 1
            return
        pool[key] = dict(
            id=hashlib.sha256("".join(key).encode()).hexdigest()[:20],
            environment=env,
            task=task,
            state=state,
            label=label,
            label_source=label_source,
            label_kind="source_derived_proxy",
            group_id=group,
            fingerprint=fp,
        )

    with manifest.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        if row["status"] != "copied":
            excluded[row["status"]] += 1
            continue
        if row.get("source_changed_during_copy", "").lower() in ("true", "1", "yes"):
            excluded["changed_during_copy"] += 1
            continue
        path = Path(row["local_path"])
        if not path.is_absolute():
            path = source_root / path
        resolved = path.resolve()
        if path.is_symlink() or not resolved.is_relative_to(source_root) or not resolved.is_file():
            excluded["unsafe_or_missing_path"] += 1
            continue
        if file_hash(resolved) != row["sha256_local"]:
            excluded["checksum_mismatch"] += 1
            continue
        group = source_group(row, salt)
        opener = gzip.open if resolved.suffix == ".gz" else open
        try:
            with opener(resolved, "rb") as source:
                content = source.read(MAX_FILE_BYTES + 1)
            if len(content) > MAX_FILE_BYTES:
                excluded["decompressed_file_cap"] += 1
                continue
            total_read += len(content)
            if total_read > 512 * 1024 * 1024:
                excluded["total_processing_cap"] += 1
                break
            text = content.decode("utf-8", errors="replace")
        except (OSError, EOFError):
            excluded["decode_or_compression_error"] += 1
            continue
        included_files += 1
        if row["environment"] == "ci-shared":
            label = ci_result(text)
            if label not in {"SUCCESS", "FAILURE"}:
                parse_counts["ci_outcome_missing_or_other"] += 1
                continue
            add(row, mask_ci_result(text), label, "jenkins_terminal_status", group)
        else:
            per_file = Counter()
            for line in text.splitlines():
                if len(line) > 65536:
                    parse_counts["oversized_line"] += 1
                    continue
                event = parse_event(line)
                if not event or event["level"] not in {"INFO", "WARN", "ERROR"}:
                    continue
                label = event["level"]
                if per_file[label] >= 300:
                    continue
                per_file[label] += 1
                add(row, mask_severity(event["message"]), label, event["label_source"], group)

    parent = {}

    def find(value):
        parent.setdefault(value, value)
        if parent[value] != value:
            parent[value] = find(parent[value])
        return parent[value]

    for a, b in linked:
        parent[find(b)] = find(a)
    candidates = [case for key, case in pool.items() if key not in conflicts]
    strata = defaultdict(list)
    for case in candidates:
        case["group_id"] = find(case["group_id"])
        strata[(case["environment"], case["label"])].append(case)
    selected, rng = [], random.Random(SEED)
    for key in sorted(strata):
        cases = strata[key]
        rng.shuffle(cases)
        buckets = defaultdict(list)
        for case in cases:
            buckets[case["group_id"]].append(case)
        groups = list(buckets)
        rng.shuffle(groups)
        chosen = []
        while groups and len(chosen) < max_per_class:
            for group in list(groups):
                chosen.append(buckets[group].pop())
                if not buckets[group]:
                    groups.remove(group)
                if len(chosen) >= max_per_class:
                    break
        selected.extend(chosen)
    selected = grouped_split(selected, seed=SEED)
    selected.sort(key=lambda case: case["id"])
    with (outdir / "cases.jsonl").open("w", encoding="utf-8", newline="\n") as output:
        for case in selected:
            output.write(json.dumps(case, ensure_ascii=False) + "\n")
    counts = Counter(f"{c['environment']}/{c['label']}/{c['split']}" for c in selected)
    report = dict(
        seed=SEED,
        label_kind="source_derived_proxy",
        warning="Native severity/build-status proxies; not human-verified incident or recovery decisions.",
        source_manifest_sha256=file_hash(manifest),
        included_files=included_files,
        excluded_files=dict(excluded),
        parse_counts=dict(parse_counts),
        redaction_counts=dict(redactor.counts),
        cases=len(selected),
        candidate_counts=dict(Counter(f"{c['environment']}/{c['label']}" for c in candidates)),
        conflicting_templates=len(conflicts),
        selected_counts=dict(sorted(counts.items())),
        independent_groups=len({c["group_id"] for c in selected}),
        cases_sha256=file_hash(outdir / "cases.jsonl"),
        missing_environment="Dedicated test not collected. Shared CI is not test.",
    )
    (outdir / "dataset_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    with (outdir / "annotation_template.csv").open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.DictWriter(
            output,
            fieldnames=["id", "environment", "state", "human_incident", "human_action", "notes"],
        )
        writer.writeheader()
        for case in selected:
            writer.writerow({key: case[key] for key in ("id", "environment", "state")})
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("manifest", "source-root", "outdir", "salt-file"):
        parser.add_argument("--" + key, required=True)
    parser.add_argument("--max-per-class", type=int, default=60)
    args = parser.parse_args()
    salt_path = Path(args.salt_file)
    if not salt_path.exists():
        import secrets

        salt_path.parent.mkdir(parents=True, exist_ok=True)
        salt_path.write_bytes(secrets.token_bytes(32))
    print(
        json.dumps(
            prepare(
                args.manifest,
                args.source_root,
                args.outdir,
                salt_path.read_bytes(),
                args.max_per_class,
            ),
            indent=2,
        )
    )
