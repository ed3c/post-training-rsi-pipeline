# Provider preflight and destination authorization

Status: **Implemented read-only admission boundary; no provider has run**

This slice adds a no-network, no-GPU, no-subprocess admission boundary that validates the hybrid-cloud Teacher → Training → Serving → Evaluation configuration *before* any data leaves the process or any paid resource starts. It closes the first executable part of `GAP-INF-01` / `GAP-INF-02` / `GAP-INF-03`.

Passing preflight does **not** mean a provider works. It means nothing in the configuration is known to be inadmissible.

## 1. Command

```bash
post-training-rsi \
  --config configs/pipeline.example.json \
  --workspace artifacts/coevolution \
  provider-preflight \
  --target reference|teacher|training|end-to-end-rsi|end-to-end-coevolution \
  [--authorization-file <json>] \
  [--strict]
```

Exit codes:

```text
0  PASS
0  WARN in non-strict mode
2  FAIL
2  WARN promoted to FAIL by --strict
```

The command may create exactly one path:

```text
<workspace>/reports/provider-preflight.json
```

It opens no socket, starts no subprocess, calls no GPU API, touches no serving endpoint, and reads no external storage. Command executables are *resolved*, never invoked.

## 2. Targets

| Target | External Teacher | Required approvals | Benchmark texts | Authorization receipt |
|---|---|---|---|---|
| `reference` | rejected | none | not required | not required |
| `teacher` | allowed | dataset | not required | required |
| `training` | allowed | dataset, checkpoint | not required | required when Teacher is external |
| `end-to-end-rsi` | allowed | dataset, checkpoint | required | required when Teacher is external |
| `end-to-end-coevolution` | allowed | dataset, checkpoint, harness | required | required when Teacher is external |

`reference` fails if any adapter selects an external backend. That keeps the deterministic path from quietly acquiring a provider.

## 3. Backend classification

Preflight classifies each adapter without constructing or executing it, using the in-process member of each vocabulary `PipelineConfig` accepts:

```text
teacher     mock          local     | openai_compatible  external
training    mock          local     | command            external
evaluation  deterministic local     | command            external
serving     local         local     | command            external
```

A backend added later is external until this table names it, so a new provider fails closed rather than inheriting mock admission.

## 4. Checks

```text
preflight-config              config revalidates; exact config_sha256 recorded
preflight-adapter-inventory   backends classified; reference rejects external
preflight-secret-names        required credential env var name present and non-empty
preflight-teacher-url         https origin, no embedded credentials, query, or fragment
preflight-commands            executables resolve; path-like worker args warn if absent
preflight-serving-commands    deploy/teardown pairing recorded as evidence
preflight-artifact-path       workspace escape rejected for production targets
preflight-budgets             retry, timeout, cost, and budget bounds are finite
preflight-approvals           target's human review gates are enabled
preflight-benchmarks          decontamination inputs present for end-to-end targets
preflight-authorization       receipt binds this config, origin, and stage
```

Two invariants are enforced by `PipelineConfig.validate()` rather than re-decided here — serving deploy/teardown pairing, and a per-iteration budget above the total. `preflight-config` runs first, so a config violating either fails admission there. Preflight records the outcome as evidence and names the enforcing layer in `enforced_by`, so the rule has one home.

## 5. Destination authorization

```text
post-training-rsi.destination-authorization/v1
```

Transmitting Dataset content to an external Teacher requires a human-owned receipt binding:

```text
authorization_id   approved_by   approved_at   expires_at
stage              origin        data_classes  config_sha256
```

The receipt approves **exact bytes to one destination**. It is rejected when:

- `config_sha256` does not match the current configuration — re-pointing the Teacher or editing budgets after approval invalidates it;
- `origin` does not match the configured Teacher destination;
- `stage` does not authorize Teacher transmission;
- `expires_at` has passed.

This prevents a reviewer's decision about one configuration from silently covering different bytes. No secret value or private prompt/data content may appear in a receipt.

## 6. Redaction

The report records credential environment variable **names**, never values, and records the Teacher **origin**, never the full URL. A URL that embeds credentials is rejected, and the offending URL is not copied into evidence — only the origin and the list of policy problems.

## 7. Determinism

Clock, environment mapping, and executable resolver are injected, so the same configuration produces byte-identical reports:

```python
ProviderPreflight(
    config,
    workspace=workspace,
    clock=lambda: "2026-08-14T15:00:00Z",
    environment={"TEACHER_API_KEY": "..."},
    resolve_executable=lambda name: f"/usr/bin/{name}",
)
```

## 8. Explicit non-claims

This slice does not prove, attempt, or provide:

- a real Teacher API request;
- a real GPU job submission;
- a real vLLM/SGLang deployment;
- provider account or quota validation;
- enterprise IdP/MFA;
- remote object-store trust;
- automatic credential provisioning;
- production rollout.

Adapter execution remains in `adapter_runtime/`; quality decisions remain in the RSI and Co-Evolution policies; production credentials and destination authorization remain human-owned.
