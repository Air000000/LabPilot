from enum import StrEnum


class ValidationVerdict(StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    INCONCLUSIVE = "inconclusive"
    ERROR = "error"
