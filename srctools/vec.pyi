from typing import Union

class Vec:
    # The numeric magic methods are defined via exec(), so we need a stub
    # to annotate them in a way a type-checker can understand.

    def __add__(self, other: Union['Vec', tuple, int, float]) -> 'Vec': ...
    def __radd__(self, other: Union['Vec', tuple, int, float]) -> 'Vec': ...
    def __iadd__(self, other: Union['Vec', tuple, int, float]) -> 'Vec': ...

    def __sub__(self, other: Union['Vec', tuple, int, float]) -> 'Vec': ...
    def __rsub__(self, other: Union['Vec', tuple, int, float]) -> 'Vec': ...
    def __isub__(self, other: Union['Vec', tuple, int, float]) -> 'Vec': ...

    def __mul__(self, other: float) -> 'Vec': ...
    def __rmul__(self, other: float) -> 'Vec': ...
    def __imul__(self, other: float) -> 'Vec': ...

    def __truediv__(self, other: float) -> 'Vec': ...
    def __rtruediv__(self, other: float) -> 'Vec': ...
    def __itruediv__(self, other: float) -> 'Vec': ...

    def __floordiv__(self, other: int, float) -> 'Vec': ...
    def __rfloordiv__(self, other: int, float) -> 'Vec': ...
    def __ifloordiv__(self, other: int, float) -> 'Vec': ...

    def __mod__(self, other: int, float) -> 'Vec': ...
    def __rmod__(self, other: int, float) -> 'Vec': ...
    def __imod__(self, other: int, float) -> 'Vec': ...

