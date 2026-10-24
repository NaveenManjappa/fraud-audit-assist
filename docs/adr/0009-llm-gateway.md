# ADR-0009: Every model call goes through one gateway

Status: Accepted | Date: 2026-10-24

`FAA_LLM__PROVIDER` chooses `fake` (default), `openai`, `azure_openai` or `anthropic`. Agents depend only on
`LlmGateway`, which adds what every call needs:

- a total timeout per attempt, and retries with exponential backoff and jitter on transient failures only
  (timeouts, dropped connections, 408/409/429/5xx); a bad request or rejected key is never retried;
- the providers' own SDK retries are off, so attempts never multiply;
- an optional client-side rate limit and an optional fallback model when the primary fails;
- a log line and metrics per call: agent, model, Prompt Version, latency, attempts, tokens and estimated cost from
  the configured prices.

The scripted fake plays each agent from the `# Role:` line its prompt starts with, so development, demos, tests and
CI cost nothing and need no key. Settings are validated at startup; production refuses the fake.
