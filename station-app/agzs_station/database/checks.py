import enum

from sqlalchemy import CheckConstraint


def one_of(column: str, enum_type: type[enum.Enum], name: str) -> CheckConstraint:
    values = ", ".join(f"'{member.value}'" for member in enum_type)
    return CheckConstraint(f"{column} IN ({values})", name=name)
