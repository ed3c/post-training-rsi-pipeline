# Validation record contract

A validation record must include:

```text
status
branch
tested commit
tested tree
workflow or environment identity
validation timestamp
runtime versions
named gates
explicit non-claims
```

The record is evidence for its exact tested tree only. Any later code, configuration, test, or build-system change requires a new record. Documentation-only changes may cite the previous code record but still require normal link and documentation-contract checks on the current head.
