"""Volumes that stay FluentControl variables.

``wt.volume("SAMPLE_UL", 20)`` declares a FluentControl variable and returns a
:class:`Volume`: a float (so blocks, fills and checks compute with it as
usual) that also carries its FluentControl name. Arithmetic on volumes keeps
the expression (``SAMPLE_UL + BEADS_UL - 2``), and a step that receives a
volume references the variable or expression instead of a literal, so a value
edited in FluentControl carries through at run time.
"""

from __future__ import annotations

from typing import Union

_ATOM, _MUL, _ADD = 3, 2, 1


def _num(value: float) -> str:
    return f"{float(value):g}"


class Volume(float):
    """A number in microliters with the FluentControl expression that yields it."""

    expr: str
    _prec: int

    def __new__(cls, value: float, expr: str, prec: int = _ATOM) -> "Volume":
        obj = super().__new__(cls, float(value))
        obj.expr = expr
        obj._prec = prec
        return obj

    def __reduce__(self):
        return (Volume, (float(self), self.expr, self._prec))

    @staticmethod
    def _term(other) -> tuple[str, int]:
        if isinstance(other, Volume):
            return other.expr, other._prec
        return _num(other), _ATOM

    def _bin(self, other, op: str, value: float, *, reverse: bool = False) -> "Volume":
        if not isinstance(other, Volume):
            # identities keep the expression readable: X + 0, X * 1, X / 1
            if (op in "+-" and float(other) == 0 and (op == "+" or not reverse)) or                     (op in "*/" and float(other) == 1 and (op == "*" or not reverse)):
                return self
        prec = _ADD if op in "+-" else _MUL
        left, lprec = (self._term(other) if reverse else (self.expr, self._prec))
        right, rprec = ((self.expr, self._prec) if reverse else self._term(other))
        if lprec < prec:
            left = f"({left})"
        # a - (b + c), a / (b * c): the right side binds tighter when it is not atomic
        if rprec < prec or (op in "-/" and rprec == prec):
            right = f"({right})"
        return Volume(value, f"{left} {op} {right}", prec)

    def __add__(self, other):
        if not isinstance(other, (int, float)):
            return NotImplemented
        return self._bin(other, "+", float(self) + float(other))

    def __radd__(self, other):
        if not isinstance(other, (int, float)):
            return NotImplemented
        return self._bin(other, "+", float(other) + float(self), reverse=True)

    def __sub__(self, other):
        if not isinstance(other, (int, float)):
            return NotImplemented
        return self._bin(other, "-", float(self) - float(other))

    def __rsub__(self, other):
        if not isinstance(other, (int, float)):
            return NotImplemented
        return self._bin(other, "-", float(other) - float(self), reverse=True)

    def __mul__(self, other):
        if not isinstance(other, (int, float)):
            return NotImplemented
        return self._bin(other, "*", float(self) * float(other))

    def __rmul__(self, other):
        if not isinstance(other, (int, float)):
            return NotImplemented
        return self._bin(other, "*", float(other) * float(self), reverse=True)

    def __truediv__(self, other):
        if not isinstance(other, (int, float)):
            return NotImplemented
        return self._bin(other, "/", float(self) / float(other))

    def __rtruediv__(self, other):
        if not isinstance(other, (int, float)):
            return NotImplemented
        return self._bin(other, "/", float(other) / float(self), reverse=True)

    def __neg__(self):
        return Volume(-float(self), f"-{self.expr}" if self._prec == _ATOM else f"-({self.expr})", _MUL)

    def __pos__(self):
        return self

    def __repr__(self) -> str:
        return f"Volume({float(self):g}, {self.expr!r})"


def fc_value(value):
    """The value a FluentControl step field gets: a volume's expression, else the value."""
    if isinstance(value, Volume):
        return value.expr
    if isinstance(value, list):
        return [fc_value(v) for v in value]
    return value


def vround(value: Union[float, Volume], ndigits: int = 2):
    """``round`` that keeps a :class:`Volume` (its expression is exact already)."""
    if isinstance(value, Volume):
        return value
    return round(float(value), ndigits)


def per_trip(volume: Union[float, Volume], trips: int):
    """One trip's share of ``volume``: an expression for a :class:`Volume`."""
    if isinstance(volume, Volume):
        return volume if trips == 1 else volume / trips
    return round(float(volume) / trips, 2)


def num(value):
    """``float(value)``, except a :class:`Volume` stays one."""
    return value if isinstance(value, Volume) else float(value)
