"""Challenge exception hierarchy."""


class ChallengeError(Exception):
    """Base class for all challenge-facing errors."""


class InvalidPatternError(ChallengeError):
    """Raised when an RLE pattern is invalid or violates size constraints."""


class RunLimitError(ChallengeError):
    """Raised when a level has no remaining runs."""


class InvalidSubmissionError(ChallengeError):
    """Raised when a submission violates challenge rules."""


class ExecutionError(ChallengeError):
    """Raised when bgolly fails or returns an invalid result."""
