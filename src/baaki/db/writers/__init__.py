"""Thin wrappers over baaki_write.* SECURITY DEFINER functions (ARCHITECTURE.md §6.6).

Nothing is re-exported: import rules are per module (§5.3), e.g. agent/ may import only
writers.proposal; policy/ may not import writers.payment or writers.ledger.
"""
