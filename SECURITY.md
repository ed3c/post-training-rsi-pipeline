<!-- i18n-key: SECURITY; locale: en; reviewed: 2026-08-15 -->
[English](SECURITY.md) · [繁體中文](SECURITY.zh-TW.md)

# Security Policy

## Supported versions

Post-Training RSI Pipeline is pre-1.0 research or alpha software. Security fixes are applied to the latest `main` branch and, when explicitly published, the latest release. Older commits, forks, experimental branches, fixtures, and archived evidence are not supported unless a maintainer states otherwise.

## Reporting a vulnerability

Do **not** open a public Issue with exploit details, credentials, private data, or a working proof of concept.

Use GitHub's private vulnerability reporting page:

```text
https://github.com/ed3c/post-training-rsi-pipeline/security/advisories/new
```

When private reporting is unavailable, open a public Issue titled `Security contact request` containing no vulnerability details. A maintainer will establish a private channel.

Include:

- affected version, commit, component, and configuration;
- realistic impact and required preconditions;
- minimal reproduction or evidence bundle with secrets removed;
- whether the issue crosses a permission, identity, provenance, sandbox, approval, network, data, or release boundary;
- suggested mitigation, when known.

## Security scope

Security reports include dataset or checkpoint substitution, approval bypass, parent/Peak lineage corruption, provider destination or credential leakage, command-adapter injection, artifact-hash confusion, unsafe recovery, budget bypass, and any path that promotes or publishes a candidate without the declared human authority.

The following are always security-sensitive:

- command construction and subprocess boundaries;
- path normalization, symlink handling, archive extraction, and workspace ownership;
- credential, token, model-provider, network, and egress handling;
- immutable identity, digests, signatures, approvals, replay, and lineage;
- output, time, retry, memory, artifact, and cost budgets;
- release workflows, dependency provenance, and generated evidence;
- any claim that could cause a user to grant more authority than the implementation provides.

## Disclosure and remediation

Maintainers will validate the report, establish the affected boundary, and coordinate a fix and disclosure. Do not publish details before a remediation or an agreed disclosure date.

A fix must include regression coverage and must not silently weaken a fail-closed control. Security advisories describe observed scope and limitations; they do not imply that unrelated configurations are safe.

## Safe research

Good-faith research that avoids privacy violations, service disruption, data destruction, persistence, credential access, and third-party targeting is welcome. Stop testing and report immediately if real secrets or private data become accessible.

## Secrets and private data

Never commit or attach live secrets. Revoke exposed credentials rather than relying on deletion. Public Git history, Actions logs, caches, artifacts, package registries, and external model providers must all be treated as disclosure surfaces.
