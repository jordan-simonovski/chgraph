from typing import overload


def target():
    return 1


def duplicate():
    return target()


@overload
def convert(value: int) -> int: ...


@overload
def convert(value: str) -> str: ...


def convert(value):
    return value
