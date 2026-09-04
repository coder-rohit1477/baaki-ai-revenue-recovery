"""Domain error types (ARCHITECTURE.md §1, §6)."""


class BaakiError(Exception):
    """Base class for all Baaki domain errors."""


class ContractViolation(BaakiError):
    """A contract invariant was violated (e.g. constructing a PolicyDecision without the kernel token)."""


class IllegalTransition(BaakiError):
    """A state transition not present in the allowlist was attempted (§2.3, §3.3)."""


class InvariantViolation(BaakiError):
    """A structural invariant (ledger balance, projection equality, ...) does not hold."""


class UnauthorizedInvoker(BaakiError):
    """A writer raised `unauthorized_invoker` (H17) or the role lacked EXECUTE."""


class WriterRefused(BaakiError):
    """A SECURITY DEFINER writer raised a named refusal (e.g. `evidence_required`)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail
