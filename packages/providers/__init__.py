"""Thin wrappers around external services: storage, GPU, ASR, auth.

Kept deliberately thin. Phase 0 showed why this matters: Cloudflare R2 was
rejected for requiring a payment method, and Modal's free credit turned out to
be $1 rather than $30. Swapping a provider should be an afternoon, not a
rewrite.
"""
