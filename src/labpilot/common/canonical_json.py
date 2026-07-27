import json
from dataclasses import dataclass, field
from decimal import Decimal


class CanonicalizationError(ValueError):
    pass


type CanonicalScalar = str | int | Decimal | bool | None
type CanonicalValue = CanonicalScalar | list[CanonicalValue] | dict[
    str,
    CanonicalValue,
]
type CanonicalObject = dict[str, CanonicalValue]


@dataclass(frozen=True, slots=True)
class CanonicalizationLimitsV1:
    max_depth: int = 64
    max_total_nodes: int = 100_000
    max_container_entries: int = 10_000
    max_string_bytes: int = 1_000_000
    max_output_bytes: int = 8_000_000
    max_integer_digits: int = 4_096
    max_decimal_digits: int = 1_000
    max_decimal_absolute_exponent: int = 10_000


_LIMITS = CanonicalizationLimitsV1()


@dataclass(slots=True)
class _ByteBudgetWriter:
    max_output_bytes: int
    buffer: bytearray = field(default_factory=bytearray)

    def append(self, chunk: bytes) -> None:
        if len(self.buffer) + len(chunk) > self.max_output_bytes:
            raise CanonicalizationError("canonical output exceeds max_output_bytes")
        self.buffer.extend(chunk)

    def finish(self) -> bytes:
        return bytes(self.buffer)


def canonical_json_bytes(value: CanonicalValue) -> bytes:
    writer = _ByteBudgetWriter(max_output_bytes=_LIMITS.max_output_bytes)
    state = _EncoderState(limits=_LIMITS, writer=writer)
    state.encode(value, depth=0)
    return writer.finish()


@dataclass(slots=True)
class _EncoderState:
    limits: CanonicalizationLimitsV1
    writer: _ByteBudgetWriter
    nodes: int = 0

    def encode(self, value: CanonicalValue, *, depth: int) -> None:
        self.nodes += 1
        if self.nodes > self.limits.max_total_nodes:
            raise CanonicalizationError("canonical value exceeds max_total_nodes")
        if depth > self.limits.max_depth:
            raise CanonicalizationError("canonical value exceeds max_depth")

        if value is None:
            self.writer.append(b"null")
            return
        if type(value) is bool:
            self.writer.append(b"true" if value else b"false")
            return
        if type(value) is int:
            self.writer.append(self._encode_integer(value))
            return
        if type(value) is Decimal:
            self.writer.append(self._encode_decimal(value).encode("ascii"))
            return
        if type(value) is str:
            self.writer.append(self._encode_string(value))
            return
        if type(value) is list:
            self._encode_list(value, depth=depth)
            return
        if type(value) is dict:
            self._encode_object(value, depth=depth)
            return
        raise CanonicalizationError(f"unsupported type: {type(value).__name__}")

    def _encode_integer(self, value: int) -> bytes:
        bit_length = abs(value).bit_length()
        estimated_digits = 1 if bit_length == 0 else (bit_length * 30_103) // 100_000 + 1
        if estimated_digits > self.limits.max_integer_digits:
            raise CanonicalizationError("integer exceeds max_integer_digits")
        try:
            rendered = str(value)
        except ValueError as exc:
            raise CanonicalizationError("integer conversion exceeds runtime limit") from exc
        if len(rendered.lstrip("-")) > self.limits.max_integer_digits:
            raise CanonicalizationError("integer exceeds max_integer_digits")
        return rendered.encode("ascii")

    def _encode_string(self, value: str) -> bytes:
        try:
            raw = value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise CanonicalizationError("string contains an isolated surrogate") from exc
        if len(raw) > self.limits.max_string_bytes:
            raise CanonicalizationError("string exceeds max_string_bytes")
        try:
            return json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except UnicodeEncodeError as exc:
            raise CanonicalizationError("string contains an isolated surrogate") from exc

    def _encode_list(self, value: list[CanonicalValue], *, depth: int) -> None:
        if len(value) > self.limits.max_container_entries:
            raise CanonicalizationError("list exceeds max_container_entries")
        self.writer.append(b"[")
        for index, item in enumerate(value):
            if index:
                self.writer.append(b",")
            self.encode(item, depth=depth + 1)
        self.writer.append(b"]")

    def _encode_object(
        self,
        value: dict[str, CanonicalValue],
        *,
        depth: int,
    ) -> None:
        if len(value) > self.limits.max_container_entries:
            raise CanonicalizationError("object exceeds max_container_entries")
        for key in value:
            if type(key) is not str:
                raise CanonicalizationError("object keys must be strings")
        try:
            keys = sorted(value, key=lambda key: key.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise CanonicalizationError("string contains an isolated surrogate") from exc
        self.writer.append(b"{")
        for index, key in enumerate(keys):
            if index:
                self.writer.append(b",")
            self.writer.append(self._encode_string(key))
            self.writer.append(b":")
            self.encode(value[key], depth=depth + 1)
        self.writer.append(b"}")

    def _encode_decimal(self, value: Decimal) -> str:
        if not value.is_finite():
            raise CanonicalizationError("Decimal must be finite")
        if value.is_zero():
            return "0"
        _, digits, exponent = value.as_tuple()
        assert isinstance(exponent, int)
        if len(digits) > self.limits.max_decimal_digits:
            raise CanonicalizationError("Decimal exceeds max_decimal_digits")
        if abs(exponent) > self.limits.max_decimal_absolute_exponent:
            raise CanonicalizationError("Decimal exponent exceeds limit")
        rendered = format(value, "f")
        if "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".")
        return "0" if rendered == "-0" else rendered
