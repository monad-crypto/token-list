"""Narrow decoded JSON, which is untyped, to the types the scripts rely on."""

from typing import TypeVar

FieldType = TypeVar("FieldType")


def read_field(container: object, field: str, expected_type: type[FieldType]) -> FieldType:
    """Return one field of a decoded JSON object, checked against its expected type.

    Args:
        container: A decoded JSON value expected to be an object.
        field: Name of the field to read.
        expected_type: Type the field's value must have, e.g. str or list.

    Returns:
        FieldType: The field's value.

    Raises:
        ValueError: If container is not an object, lacks the field, or the field
            has another type.
    """
    if not isinstance(container, dict):
        raise ValueError(f"expected a JSON object holding {field!r}")

    value = container.get(field)

    if not isinstance(value, expected_type):
        raise ValueError(f"{field!r} must be a {expected_type.__name__}")

    return value


def read_list(container: object, field: str, item_type: type[FieldType]) -> list[FieldType]:
    """Return a field of a decoded JSON object that must be a list of one item type.

    Args:
        container: A decoded JSON value expected to be an object.
        field: Name of the list field to read.
        item_type: Type every item must have, e.g. str.

    Returns:
        list[FieldType]: The list's items.

    Raises:
        ValueError: If the field is missing, not a list, or holds another type.
    """
    items = read_field(container, field, list)
    typed_items = [item for item in items if isinstance(item, item_type)]

    if len(typed_items) != len(items):
        raise ValueError(f"every item of {field!r} must be a {item_type.__name__}")

    return typed_items


def read_object_list(container: object, field: str) -> list[dict[str, object]]:
    """Return a field of a decoded JSON object that must be a list of objects.

    Args:
        container: A decoded JSON value expected to be an object.
        field: Name of the list field to read.

    Returns:
        list[dict[str, object]]: The list's objects, with values still unchecked.

    Raises:
        ValueError: If the field is missing, not a list, or holds a non-object.
    """
    return read_list(container, field, dict)
