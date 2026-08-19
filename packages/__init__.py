"""Shared domain code, imported as `packages.core`, `packages.audio` and so on.

The prefix is not decoration. Without it these are importable as bare `core`,
`audio`, `lyrics` and `providers` - names that also exist on PyPI, and which
made packages/ resolvable under two different module names at once, which mypy
rightly refused to accept.
"""
