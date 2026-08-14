"""Model/Harness Co-Evolution component namespaces.

Import concrete contracts from explicit subpackages rather than this package root:

- ``post_training_rsi.harness.outer_loop``
- ``post_training_rsi.harness.trace_harvesting``

The root package intentionally performs no eager imports. This keeps partially
stacked components importable without inventing compatibility modules or
creating circular dependencies between the outer, middle, and inner loops.
"""

__all__: list[str] = []
