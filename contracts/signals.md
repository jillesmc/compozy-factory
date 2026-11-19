# Contract: `signals` (observability)

The only role that takes a provider list (`[roles.signals] provider = [...]`). Each provider has its
own adapter. Findings travel as the provider-neutral `factory.finding/v1` envelope
(`tools/factory_envelope.py`); the factory, not the adapter, computes the fingerprint.

| Operation | Input | Output | Errors |
| --- | --- | --- | --- |
| signals.list_new | — (daemon heartbeat sources declared in the sentinel's HEARTBEAT.md) | new or grown items [{key, provider fields}] | signals_unavailable |
| signals.normalize | one item | envelope fields {provider, provider_finding_id, fingerprint_inputs {service, error_class, location}, evidence[], verifier {provider, payload}} | normalize_failed |
| signals.query_window | query, since, until (ISO UTC or epoch ms) | evidence {query, since, until, summary, rows} | query_invalid, signals_unavailable |
| signals.verify_gone | envelope, deploy time, window (days) | {verdict confirmed/regressed/inconclusive, before, after, traffic, evidence[]} | query_invalid, signals_unavailable |

`signals.verify_gone` is always performed by the adapter of `envelope.verifier.provider`, whatever
providers are configured today.
