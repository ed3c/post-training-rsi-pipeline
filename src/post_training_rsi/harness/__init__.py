"""Model/Harness Co-Evolution component namespaces.

Import concrete contracts from explicit modules or subpackages rather than
this package root:

- ``post_training_rsi.harness.outer_loop``
- ``post_training_rsi.harness.trace_harvesting``
- ``post_training_rsi.harness.model_inner_loop``
- ``post_training_rsi.harness.coevolution_store``

The root package intentionally performs no eager imports. This keeps stacked
outer-loop, trace-harvesting, model-inner-loop, and convergence components
importable without inventing legacy compatibility modules or creating circular
dependencies between the loops.
"""

__all__: list[str] = []
