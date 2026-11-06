# Frozen chat-LLM log classification comparison

Prepared 2026-11-06 before real holdout inference. Exploratory, source-derived labels.

- Models: qwen3.5:9b, gemma4:12b, ornith-1.5:9b through local Ollama;
  gpt-6-luna through Chat Completions; deepseek-flash through native Chat;
  gemini-3.5-flash-lite through generateContent. No silent substitutions.
- Same 158 test cases from the previous Decision study, SHA256 of the complete
  339-case export: 3ccaaf86b43c9069b54ac10b7890609bbcfb169ef9db1eeaef987a7ae39efcd0.
  No new split, model tuning, raw logs, salt, credentials or source paths in upload.
- Prod 29 (INFO20/WARN6/ERROR3), dev 76 (WARN60/ERROR16), CI 53 (SUCCESS38/FAILURE15).
  One source group for prod and one for dev: samples are not independent incidents.
- Three repeat blocks, seed 2026+block for model/case order; concurrency one and
  one loaded Ollama model. A100 80GB + High RAM; save actual hardware, Ollama version,
  model metadata/digests, returned API model version and token usage.
- All receive identical English question, class definitions, redacted log and JSON
  schema in prompt. No gold labels, group, split or provenance sent as model inputs.
  The generated JSON contains a class and self-reported probabilities. Validate without
  repair, clipping, renormalization or model retries. These probabilities are not native
  Decision posteriors; do not treat their calibration as the same quantity.
- Local context 4096, 256 output tokens, temperature 0 and thinking disabled;
  OpenAI reasoning none (no temperature), DeepSeek thinking disabled;
  Gemini minimal thinking. These are deployed configurations, not equal FLOPs.
  Native JSON constraints differ by provider; save that distinction.
- Two synthetic warmups per model/block, excluded from real-case latency. At most
  3000 total attempts (2880 planned including warmups), no automatic retries.
  Stop affected model on 400/401/403/404/422/429 or 3 consecutive invalid/network errors.
  No unlimited calls, purchases, new credentials, public tunnels or VPS activity.
- Journal pending before each call and completion after it. Resume skips ambiguous
  pending requests rather than silently rebilling. Changing data/prompt/source requires
  a separate output directory and protocol amendment before interpreting new results.
- Primary quality: mean of fixed-label macro F1 across environments and repeat blocks;
  dev also reports supported-label F1 because INFO is absent. Errors count as incorrect.
  Rank only complete planned runs. Missing API access is unavailable, never quality zero.
- Secondary: class support/recall, accuracy, valid/planned coverage, warm p50/p95
  latency and provider token usage. Repeats are repeated measurements, not 474
  independent cases. Latency includes network; do not infer API VRAM or monetary bills.
- Prior Decision results are historical references on the same dataset, not concurrent
  randomized measurements. LLM native chat and Decision APIs are different workloads.
  This cannot prove RCA correctness, recovery safety or a universal production winner.

References accessed 2026-11-06:

- https://ollama.com/library/qwen3.5:9b
- https://ollama.com/library/gemma4:12b
- https://ollama.com/library/ornith-1.5:9b
- https://docs.ollama.com/api/chat
- https://developers.openai.com/api/docs/models/gpt-6-luna
- https://api-docs.deepseek.com/quick_start/pricing
- https://api-docs.deepseek.com/guides/json_mode/
- https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite
- Kassis, T., Agarwal, V., He, Y., Patel, D., & Brueckner, A. M. (2026).
  Scientific Agent Skills: A Library of Procedural Knowledge for Research Agents.
  https://doi.org/10.48550/arXiv.2609.00065 (record checked: latest revision 2 Sep 2026).
  Procedural assistance in experimental design; not evidence of model performance.
