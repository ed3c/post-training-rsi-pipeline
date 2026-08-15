<!-- i18n-key: OSS_CHECKLIST; locale: en; reviewed: 2026-08-15 -->
[English](OPEN_SOURCE_CHECKLIST.md) · [繁體中文](OPEN_SOURCE_CHECKLIST.zh-TW.md)

# Open-source readiness checklist

This checklist defines the public repository baseline for Post-Training RSI Pipeline. A checked document means the policy is present; it does not claim that every external runtime, release lane, or production control has been exercised.

## Project identity

- [x] Clear purpose, audience, status, quick start, architecture, limitations, and non-goals in `README.md`
- [x] English and Traditional Chinese public landing pages
- [x] Machine-readable package metadata where the project is packaged
- [x] Repository owner and maintainers documented
- [x] License status and third-party content boundaries documented

## Community health

- [x] `CONTRIBUTING.md`
- [x] `CODE_OF_CONDUCT.md`
- [x] `SUPPORT.md`
- [x] `GOVERNANCE.md`
- [x] Issue and Pull Request guidance
- [x] AI-assisted contribution accountability

## Security and privacy

- [x] Private vulnerability reporting route
- [x] Supported-version policy
- [x] Secret and private-data handling rules
- [x] Fail-closed evidence and claim boundaries
- [x] Project-specific threat or trust documentation linked from the docs index

## Engineering quality

- [x] Repeatable local validation command
- [x] CI entrypoint
- [x] Tests and static checks
- [x] Exact implementation/evidence status is kept separate from roadmap
- [x] Generated artifacts and provenance are separated from authoritative source
- [ ] Reproducible release provenance for every published artifact — required when a release channel is enabled
- [ ] Published SBOM for every release artifact — required when packaged releases are published

## Documentation

- [x] Documentation index
- [x] State Machine or architecture ownership documentation
- [x] English/Traditional Chinese language policy
- [x] CI structural validation for maintained translation pairs
- [x] Controlled exceptions for executable Agent contracts and immutable evidence
- [ ] Semantic translation review by a second fluent reviewer — recommended before a stable release

## Release and operations

- [x] Changelog policy
- [x] Release procedure
- [x] Human release authority
- [x] Rollback expectations
- [ ] Stable compatibility and deprecation policy — deferred until the project reaches a stable API release
- [ ] Production support commitment — not offered by this open-source baseline

## Review rule

Do not check an item because a file or workflow merely exists. Check it only when the stated policy or mechanism is usable and its limitations are explicit.
