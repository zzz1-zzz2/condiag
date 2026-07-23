"""P1-3D: Acquisition module.

Bounded, evidence-linked retrieval that executes SearchAction contracts
against a real repo. v1 supports only two action types:

  - FIND_DEFINITION: AST-based symbol lookup
  - FIND_RELATED_TESTS: ranked test discovery

Other action types return UNSUPPORTED status.
"""
