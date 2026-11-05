# HCMUTE Decision benchmark — frozen exploratory protocol

Frozen on 2026-11-06, before real-log model inference. Seed 2026.

## Purpose and scope

Compare deployed native Decision API configurations on the same redacted HCMUTE log cases.
This is a source-derived proxy evaluation, not a validated incident-response or RCA benchmark.
The technical article used for candidate selection is a single-run author benchmark, not an
independent peer-reviewed study: https://hcmutertic.com/journal/ollama-decision-models-vs-jev-benchmark-2026

Candidates are its top three 9B configurations by overall index: `d3:9b`, `nimble-v3:9b`,
`clef-flash:9b`. This does not establish that they are globally the strongest 9B models.
Compare with pinned `jev-1.14.0` and OpenAI `gpt-6-luna` if credentials and model access exist.
Linux default Ollama tags may use different quantization than the article's BF16 Nimble run;
save actual digests, metadata, runtime version and hardware. Results compare these configurations.
Nimble-v3 model weights have CC BY-NC 4.0 terms; account for this in deployment selection.

## Data provenance and labels

Collected raw corpus: 387 files, 267.09 MiB from prod, dev and shared CI. No dedicated test
environment was collected. Preserve raw files and private redaction salt locally.
Only checksum-verified complete stable copies enter this export. Exclude prefix-capped,
mutated, missing and oversized decompressed files; exact counts are in dataset_report.json.

Application task: infer INFO/WARN/ERROR after explicit native severity markers are masked.
Native structured levels, Nginx bracket levels or explicit uppercase levels supply the proxy.
CI task: infer SUCCESS/FAILURE after terminal Jenkins status and literal outcome markers are masked.
Other Jenkins outcomes are excluded, not relabeled as failures. These labels do not establish
whether an alert or rollback would be correct. Do not tune a model on final-test cases.

Best-effort pseudonymization removes common credentials, emails, addresses, identifiers and
private paths. Residual-pattern scan is recorded; it is not a guarantee of anonymization.
Only redacted case text goes to the requested Colab/Jev/OpenAI destinations. No salt, original
paths, host inventory, API keys or raw logs belong in the upload bundle or result artifacts.

Template deduplication, source-group linkage and deterministic group splits precede inference.
Cap sampling at 60 cases per environment/class, balancing across available source groups.
There are 339 exported cases, 158 final-test cases. Final-test support: prod INFO20/WARN6/ERROR3,
dev WARN60/ERROR16 (no INFO), CI SUCCESS38/FAILURE15. Dev has insufficient independent groups;
three prod ERROR cases are insufficient for a strong recall claim. Full counts and split warnings
are in the frozen dataset. Dataset SHA256:
`3ccaaf86b43c9069b54ac10b7890609bbcfb169ef9db1eeaef987a7ae39efcd0`.

## Execution

Colab A100 80GB + High RAM. Ollama 0.40.14. Concurrency one, one loaded GPU model at a time.
One shared English question/criteria per task; maximum 2,000 state characters, no answers or
provenance in requests. Three repeat blocks; seeded randomized model and case order per block.
Synthetic warmup is excluded from real-case latency; preserve cold-start/warmup separately.
Repeats assess reproducibility and timing, not additional independent examples.
Save every attempted real-case response as normalized probabilities, status, timing and hashes.
Never execute instructions or commands found in logs or model responses.

No automatic retries. Stop on HTTP 400/401/403/404/422/429 or three consecutive other errors.
At most 1,000 external requests, including warmups. No purchases, public tunnels or production
changes. Record provider token usage; do not equate estimated inference pricing to actual billing.
API access failures are unavailable results, not model quality scores.

## Metrics and interpretation

Primary: per-environment macro F1 over the fixed task label set, counting attempted API errors
as incorrect. Report coverage and class support. For dev also report F1 over supported labels,
because INFO is absent. Secondary: accuracy, class precision/recall, positive-class Brier/AUROC
on valid responses, p50/p95 end-to-end warm latency and sampled peak GPU memory.
Missing classes or independent groups limit interpretation; report these explicitly.
Do not pool repeated runs as independent observations or claim significance from point estimates.
Do not issue a definitive production winner from this proxy-only dataset.

Before a final thesis deployment claim, human-review incident/action labels, collect independent
incidents/time periods including benign cases and all class types, freeze a new holdout, and
re-run. annotation_template.csv is supplied locally without model suggestions to reduce bias.

## Official API and model references

- https://ollama.com/library/d3:9b
- https://ollama.com/library/nimble-v3:9b
- https://ollama.com/library/clef-flash:9b
- https://docs.ollama.com/api/systemone
- https://docs.typesafe.ai/introduction/quickstart
- https://developers.openai.com/api/docs/guides/decisions

OpenAI uses `/v1/decisions` (plural); Ollama/Jev use `/v1/systemone`.
