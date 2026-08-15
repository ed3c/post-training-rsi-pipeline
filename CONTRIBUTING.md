<!-- i18n-key: CONTRIBUTING; locale: en; reviewed: 2026-08-15 -->
[English](CONTRIBUTING.md) · [繁體中文](CONTRIBUTING.zh-TW.md)

# Contributing to Post-Training RSI Pipeline

Thank you for improving Post-Training RSI Pipeline. Contributions are reviewed against correctness, safety, evidence quality, maintainability, and documentation clarity.

## Before opening a change

1. Search existing Issues and Pull Requests.
2. For non-trivial work, open or reference an Issue that states the user-visible outcome, affected trust boundary, acceptance tests, and explicit non-goals.
3. Never place credentials, private source material, customer data, proprietary repository content, or production artifacts in an Issue, prompt, fixture, log, or commit.
4. Report vulnerabilities through [SECURITY.md](SECURITY.md), not a public Issue.

## Development setup

```bash
git clone https://github.com/ed3c/post-training-rsi-pipeline.git
cd post-training-rsi-pipeline
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

Run the repository gate before requesting review:

```bash
make lint && make typecheck && make test
```

Use the exact commands documented by the changed subsystem when it has additional smoke, schema, replay, or integration checks.

## Change design

Keep each Pull Request independently reviewable:

- one primary outcome and one clear rollback boundary;
- the smallest practical path lease;
- tests for success, rejection, tamper, timeout, and recovery paths where applicable;
- no claim stronger than the captured evidence;
- no automatic widening of permissions, network access, secrets, model authority, release authority, or side effects;
- update English and Traditional Chinese public documentation in the same Pull Request.

Generated artifacts and evidence receipts must be reproducible, bounded, scrubbed of secrets, and clearly separated from source code.

## Pull Request requirements

The description must include:

- problem and intended outcome;
- scope and non-goals;
- architecture or State Machine impact;
- security, privacy, and compatibility impact;
- commands executed and observed results;
- rollback plan;
- documentation and translation changes;
- linked Issue.

Draft Pull Requests are preferred until the implementation and evidence are ready for review. A green workflow is evidence for that workflow only; it is not production, security, model-quality, or external-runtime proof.

## AI-assisted contributions

AI tools may assist with analysis, code, tests, or documentation. The human contributor remains accountable for every submitted byte and must:

- review the full diff;
- run the declared validation;
- disclose material AI assistance in the Pull Request;
- prevent private or restricted data from being sent to an unapproved provider;
- remove fabricated citations, unverifiable claims, and prompt-derived authority;
- preserve repository-specific Agent and security contracts.

## Commit and review discipline

Write descriptive commits. Do not bypass hooks or checks. Do not mix unrelated formatting, generated output, dependency upgrades, and behavior changes unless the coupling is required and explained.

Maintainers may request smaller slices, stronger negative controls, clearer evidence, or a narrower claim before merge.

## Licensing

By submitting a contribution, you agree that your contribution may be distributed under this repository's license and that you have the right to submit it. Third-party material must include its license, provenance, and any required notices.
