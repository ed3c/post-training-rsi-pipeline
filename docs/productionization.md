# Productionization checklist

The repository implements the control plane and evidence contracts. The default adapters are deterministic and do not perform real gradient updates.

## Required replacements for real post-training

- Replace `MockTeacherClient` with a batch-capable inference provider or an internally hosted vLLM/SGLang endpoint.
- Replace `TokenJaccardIndex` with Sentence-Transformers plus FAISS/HNSW for large historical datasets.
- Replace `MockTrainer` with `CommandTrainer` connected to TRL/DeepSpeed, a managed GPU job, or a training platform.
- Replace `DeterministicEvaluator` with Inspect AI, lm-eval, or an internal Agent benchmark environment.
- Add a serving adapter for vLLM/SGLang and ephemeral endpoint lifecycle management.
- Mirror local lineage manifests to MLflow and DVC/lakeFS.

## Security controls

- Execute generated code only in an isolated sandbox with filesystem, process, network, time, and memory limits.
- Keep benchmark data in a read-only store that is not available to teacher-generation prompts.
- Redact secrets from prompts, traces, stdout/stderr, and tracking artifacts.
- Use provider quotas in addition to the application budget ledger.
- Require human approval before promoting changes that affect production permissions, tool schemas, or high-impact actions.
- Sign model artifacts and verify hashes before evaluation or deployment.

## Reliability controls

- Run training and evaluation as durable jobs with retries at the orchestration layer, not inside an unbounded Agent loop.
- Persist checkpoints before Spot-instance termination.
- Make dataset acceptance atomic and immutable.
- Use shadow evaluation and canary traffic before replacing a production endpoint.
- Track task-family scores, not only one aggregate score, to detect capability regressions.

## Data-science work still required

The example thresholds are starting points, not universal constants. Calibrate entropy, Distinct-N, similarity, overlap, LCS, and promotion thresholds against labelled false-positive/false-negative sets for the target domain. Add teacher diversity, source balancing, curriculum coverage, and held-out generalization metrics before large-scale training.

## Suggested deployment sequence

1. Run the deterministic demo in CI.
2. Connect a real teacher endpoint while keeping mock training/evaluation.
3. Connect a sandboxed evaluator and validate failure trajectories.
4. Launch LoRA/QLoRA training on a small model.
5. Add DVC/lakeFS and MLflow lineage mirrors.
6. Enable co-evolution with human approval at model and Harness promotion gates.
7. Increase autonomy only after rollback, cost, and audit drills pass repeatedly.
