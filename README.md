# loupe

**See what your agent did.** Loupe records actual agent runs as parent-linked spans, then makes latency, failure points and known token costs inspectable in a React dashboard.

Python SDK → bounded background batch export → Express ingestion → MongoDB aggregation → React trace list and waterfall.

## Run locally

Node 22+, Python 3.10+, internet for dependency installs and the first temporary MongoDB binary download:

```sh
npm ci
python3 -m pip install -e './sdk[openai,anthropic,langchain]'
npm run build
npm start
```

Open http://127.0.0.1:4173. In another terminal:

```sh
python3 demo/agent.py
```

The demo runs eight real local document-retrieval tasks and exports 30 spans. Two runs deliberately fail with a tool timeout so you can inspect failure points. It **does not call an LLM or invent provider tokens** by default. A provider run is optional:

```sh
# Set OPENAI_API_KEY securely in your own environment first. Calls may cost money.
python3 demo/agent.py --openai --runs 2
```

The local demo uses a real temporary MongoDB 7 process, not a mocked database. Records reset when it stops. For persistent storage set `MONGODB_URI` to a MongoDB 7+ instance. No hosted deployment has been configured.

## Instrument an agent

Install from the repo with `pip install ./sdk`, or install an optional provider extra above. Not published to PyPI yet.

```python
from loupe import configure, trace, patch_openai
client = configure(service_name="research-agent")
patch_openai()  # opt-in; patches sync/async OpenAI create methods

@trace("research run")
def research():
    return lookup()

@trace("execute_tool lookup", attributes={"gen_ai.tool.name": "lookup"})
def lookup():
    return "result"

research()
client.flush()  # explicit bounded lifecycle wait, not on the hot path
client.close()
```

`patch_anthropic()` wraps sync/async Messages.create. Patches are idempotent and return an undo function. LangChain: `from loupe.langchain import LoupeCallbackHandler`; pass an instance via `config={"callbacks": [LoupeCallbackHandler()]}`. Async decorators use ContextVars; callback runs use explicit parent run IDs.

## Trace schema and semantics

A run is one trace; each tool, LLM call or nested function is a span. IDs use OTel lengths: 32 hex chars for trace IDs, 16 for span IDs. Fields include `parent_span_id`, `kind`, timestamps, status, resource metadata and attributes. `Span.traceparent` exposes W3C formatting for future propagation support.

LLM patches use `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.usage.input_tokens`, and `gen_ai.usage.output_tokens`. Exceptions record `error.type`, not their potentially sensitive message.

This is an **OTel-shaped span schema**, not an OTLP receiver/exporter or a full OpenTelemetry SDK. No claim of drop-in Collector compatibility. GenAI conventions are still in development; the reference used is [the official GenAI span spec](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md).

## Export reliability

A bounded in-memory queue and daemon worker keep network export off the agent hot path. Batches retry twice by default with bounded backoff and timeouts; overflow, shutdown timeout and exhausted retries are visible in diagnostics. Ingestion deduplicates `(trace_id, span_id)` with a unique index, making re-export safe. No disk spool: a crash can lose buffered spans. Export failures never replace a wrapped function's return value or exception. Explicit `flush()` and `close()` may wait within their timeouts.

## Analytics and cost

Mongo indexes cover trace ID, started time and status. Aggregation first groups spans into traces, then computes successful/error/incomplete runs, approximate p95 wall-clock latency, raw tokens, known cost totals and daily usage. Trace wall time is earliest start to latest end, not a sum of overlapping spans. Lists show the newest 100 traces; aggregates cover the selected service/status over the last seven days.

A pricing module computes estimates at ingest and stores raw counts plus pricing version for later recomputation. `PRICING_FILE` accepts:

```json
{"version":"reviewed-2026-10","models":{"your-exact-model":{"input_per_million":1,"output_per_million":2,"source":"your verified rate source"}}}
```

These numbers are an **example**, not current provider rates. The default table only prices `loupe-local-demo` at $0 because it is local. Unknown models, missing usage and cache-token cases remain unpriced. The dashboard explicitly distinguishes known costs from unpriced LLM spans. This is not an invoice or a complete billing engine; reasoning, batch, cache, multimodal and provider-specific pricing need reviewed rules. Price changes do not mutate old estimates silently. Recompute by reading original usage with a chosen pricing version.

## API

- `POST /api/spans` with `{ "spans": [...] }`, at most 100 spans per batch
- `GET /api/traces?status=ERROR&service=docs-agent&days=7`
- `GET /api/traces/:trace_id`
- `GET /api/stats` and `GET /api/services`
- `GET /api/health`

If `LOUPE_API_KEY` is set, all API routes require `Authorization: Bearer ...`. The dashboard accepts a key into memory only. Local mode binds to loopback. Production refuses to start without both a persistent database and API key. TLS, retention, workspace RBAC and operational monitoring are still needed before a shared hosted rollout.

## Tests

```sh
npm run test:sdk
npm test
npm run test:ui
```

SDK tests verify nested/async contexts, export failure isolation, bounded overflow, exception preservation, privacy, provider patch imports/undo, and LangChain parenting. API tests use isolated MongoDB and cover auth, validation, duplicate batches, indexes, trace metrics and unknown pricing. Browser tests execute the demo, inspect traces and errors, and check desktop/mobile layouts. Set `CHROME_PATH` if Chrome is not at `/usr/bin/google-chrome`.

## Scope and next steps

- Nonstreaming calls only in v0.1; streaming calls are passed through unchanged and uninstrumented.
- Provider usage parsing is tested with fixtures and installed client method patches. Live provider inference requires your own key and is not part of the no-key test suite.
- No prompt, response, function arguments or tool output capture by default. Custom attributes are your responsibility: do not send secrets or personal data.
- Single workspace, API-key access, no durable export spool, no inbound remote traceparent continuation, no OTLP adapter yet.
- At larger volumes, move aggregates to a columnar store and add sampling, retention and pagination.

### Static website preview
GitHub Pages builds the chosen dark dashboard with `VITE_STATIC_PREVIEW=true`.
The current preview uses 64 explicitly simulated task runs, including LLM spans, tokens and illustrative costs. The single badge identifies it as simulated and read-only; no provider executions or paid charges are claimed. Previous-period deltas and range filters are computed from this fixture anchored to October 5, 2026. Rates are illustrative, not actual provider prices. It supports chart selection, custom filters, range selection and span inspection. Normal builds still use the real API, show real ingested spans and do not invent period comparisons. Regenerate the synthetic preview with `node tests/generate-rich-preview.mjs`. The earlier real tool-only capture script is retained separately.

## Live backend
The backend now accepts OTLP/HTTP JSON at `/v1/traces`, scoped read/write keys, exact CORS origins, cursor pagination, prior-period stats and receive-time retention. See [production notes](docs/production.md) for deployment, auth limits and hosting choices. It is a tested single-workspace portfolio backend, not a claim of high-volume production validation. The public Pages site remains simulated until a host is selected.
