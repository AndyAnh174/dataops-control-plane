# Frozen code embedding and fixed-reader comparison

Prepared 2026-11-06 before embedding inference. Exploratory code navigation study.

Public corpus only: Control Plane 1e1674614c878fc1f6e4e19ca6d1192dac663ddd and
Agent c1570c5c1d6d3f9e754541c41885dd6ef127f83c. No prod logs, credentials, local
uncommitted code or secrets in the corpus. 49 source files, 166 windows of at most
2000 characters with overlap 250. Freeze corpus/query SHA256 in data/manifest.json.

Twenty-four manually authored intents, each in English and Vietnamese (48 queries).
Gold locations point to source-verified implementing function declarations;
every relevant window covers the declaration span. The paired translations are
one intent group, not 48 independent tasks. Labels are provisional agent-assisted
navigation judgments; the student must review them before a strong thesis claim.
Neither model output nor test scores are used to generate or tune labels.

Candidates and native dimensions (expanded before any embedding inference):

| Provider | Model | Dimensions |
|---|---|---:|
| Ollama | bge-m3:567m | 1024 |
| Ollama | qwen3-embedding:0.6b | 1024 |
| Ollama | qwen3-embedding:8b | 4096 |
| OpenAI | text-embedding-3-small | 1536 |
| OpenAI | text-embedding-3-large | 3072 |
| Voyage | voyage-code-4 | 1024 |
| Gemini | gemini-embedding-001 | 3072 |
| Gemini | gemini-embedding-2 | 3072 |

This compares deployed configurations including native dimension and quantization,
not architectures at equal dimension/FLOPs. Gemini Embedding 001 is a batch-capable
baseline with explicit CODE_RETRIEVAL_QUERY; not a claim of latest Gemini model.
Embedding 2 is also tested using code-retrieval prompt prefixes and one text per
request. Never pass different code samples as parts of one embedding: that would
aggregate them instead of returning independently scored vectors. The BM25 corpus,
queries and scores were not changed when adding these stronger API alternatives.
BM25 (k1=1.5,b=.75) is a concurrent lexical baseline on the same code windows.
Ollama models receive no raw instructions from code as executable commands.

Use A100 High RAM, Ollama 0.40.14, concurrency one, one loaded local model.
The native Decision reader uses a derived local tag FROM d3:9b with num_ctx=16384,
preserving the base weights while accepting all five passages. Embedding requests
explicitly use 4096 context tokens. This also permits reuse of the retained Colab
VM without restarting its original Ollama server. SystemOne never truncates input;
context errors are recorded instead of silently dropping passages. The separate
embedding notebook can execute via nbclient in the shared A100 VM while the cloud
LLM client runs; each study remains sequential and only one local model is loaded.
Record this overlap when interpreting client-side latency.
Warm up one synthetic query per model. Index identical document batches of up to 32;
measure single-query calls in three seeded repeat blocks. Use Qwen retrieval
instruction on queries, Voyage document/query input_type, Gemini code-query/document
task types, native OpenAI embedding input. No prompt tuning after results.
Normalize vectors and validate finite/nonzero dimensions and batch alignment.
Float32 storage; exact cosine ranking with deterministic ID tie break. No ANN,
hybrid search, reranking or per-model chunking to confound the embedding comparison.

Primary: Recall@5, MRR@5, nDCG@5, overall and EN/VI. Secondary: warm query p50/p95,
index wall time, token usage and float32 vector size. Index/query delay is recorded
separately from API timing. Repeats are repeated measurements; they do not triple n.
Rank only complete configurations; missing keys or quota errors are unavailable.

Downstream: keep two fixed readers across every complete first-block retrieval:
gpt-6-luna Chat Completions and d3:9b native Decision /v1/systemone. Both select one
of the same top-five passages or NONE. Oracle context (one gold + seeded distractors)
is a separate upper control. No gold target IDs appear in model input. Score selected
passage relevance and coverage; the LLM also emits a short answer, whose semantic
quality is not automatically validated. Do not label this metric full code-QA accuracy.
No deployment, recovery execution or code execution based on generated output.

Google embedding calls use a persistent conservative rolling budget: 90 requests
and 27,000 scheduling units per 62 seconds, below the observed free-tier 100 RPM /
30K TPM. A unit is one UTF8 byte plus 128 overhead per input; it is an upper-bound
scheduling proxy, not measured token usage. Gemini 001 documents use batches of 4;
Embedding 2 uses one document per request. Waiting occurs before the HTTP timer,
is saved separately, and remains included in total index wall time. Limits for the
provided key may differ; no billing or quota settings are changed.

At most 2000 embedding attempts plus 1000 reader attempts; planned upper bound
is about 2364 requests total, of which fewer than 1450 are cloud calls. Checkpoint
pending/completed attempts and vector cache hashes; no automatic retries or hidden
fallbacks. Stop a failing configuration on access/quota/format errors. This request
budget is not a monetary spending limit or a measured bill.

No universal "strongest model" claim. Report the best complete candidate for this
corpus/task, the quality-latency-storage tradeoff, and unknown cloud cost when pricing
or billing is unverified. Small, public, project-specific code corpus and related
queries limit generalization and permit training-data contamination.

Official references checked 2026-11-06:

- https://docs.ollama.com/api/embed
- https://ollama.com/library/bge-m3:567m
- https://ollama.com/library/qwen3-embedding:0.6b
- https://ollama.com/library/qwen3-embedding:8b
- https://developers.openai.com/api/docs/guides/embeddings
- https://docs.voyageai.com/docs/embeddings
- https://ai.google.dev/gemini-api/docs/embeddings
- Kassis, T., Agarwal, V., He, Y., Patel, D., & Brueckner, A. M. (2026).
  Scientific Agent Skills: A Library of Procedural Knowledge for Research Agents.
  https://doi.org/10.48550/arXiv.2609.00065 (latest revision checked: 2 Sep 2026).
  Procedural design assistance, not performance evidence.
