<!-- i18n-key: RELEASING; locale: en; reviewed: 2026-08-15 -->
[English](RELEASING.md) · [繁體中文](RELEASING.zh-TW.md)

# Releasing Post-Training RSI Pipeline

A release is a human-governed publication event. Passing tests, a merged Pull Request, a signature, or an artifact upload does not independently authorize a release.

## Preconditions

1. Select an exact commit on the protected release lineage.
2. Confirm the version, changelog, compatibility impact, migrations, and rollback plan.
3. Run the repository gate:

```bash
make lint && make typecheck && make test
```

4. Run any domain-specific smoke, schema, replay, packaging, or external-runtime checks required by the release scope.
5. Review dependency and license changes, generated artifacts, secrets, provenance, and security findings.
6. Update English and Traditional Chinese public release documentation.
7. Obtain explicit human approval from a maintainer with release authority.

## Publication

- Create an annotated version tag only from the admitted commit.
- Build artifacts from the tagged source in the controlled release workflow.
- Record artifact digests and provenance where the workflow supports them.
- Publish release notes that separate implemented behavior, verified evidence, known limitations, and planned work.
- Never describe fixture, mock, local, CI, emulator, sandbox, or production evidence as interchangeable.

## Post-release

Verify published artifacts, links, package metadata, and installation instructions from a clean environment. If the release is unsafe or materially incorrect, stop distribution, publish an advisory, and follow the documented rollback or replacement process.
