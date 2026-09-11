"""Proportionate completion checks for delegated Agent results."""

from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from short_drama.agent_runtime.models import AgentResult, AgentResultStatus, WorkOrder


class AgentResultContractError(ValueError):
    """Raised when a completed result does not satisfy its WorkOrder contract."""


def validate_agent_result_contract(work_order: WorkOrder, result: AgentResult) -> None:
    """Validate objective completion without pretending to judge creative quality.

    Partial and failed results may intentionally be incomplete.  Completed results
    must be internally complete, expose some structured completion evidence when
    prose acceptance criteria were supplied, and match the supported JSON Schema
    subset in ``expected_output_schema``. New WorkOrders describe the dedicated
    ``deliverable`` object; legacy results without one retain the original
    whole-AgentResult validation behavior.
    """

    if result.status != AgentResultStatus.COMPLETED:
        return
    if result.unresolved_questions:
        raise AgentResultContractError(
            "A COMPLETED AgentResult cannot contain unresolved_questions"
        )
    if work_order.acceptance_criteria and not _has_completion_evidence(result):
        raise AgentResultContractError(
            "A COMPLETED AgentResult with acceptance_criteria must include at least "
            "one finding, decision, artifact update, evidence reference, or deliverable"
        )
    if work_order.expected_output_schema:
        payload = (
            cast(JsonValue, result.deliverable)
            if result.deliverable
            else cast(
                JsonValue,
                result.model_dump(
                    mode="json",
                    exclude={"id", "work_order_id", "created_at"},
                ),
            )
        )
        _validate_schema(payload, work_order.expected_output_schema, path="AgentResult")


def _has_completion_evidence(result: AgentResult) -> bool:
    return bool(
        result.created_artifacts
        or result.deliverable
        or result.updated_artifacts
        or result.findings
        or result.decisions
        or result.evidence_refs
    )


def _validate_schema(
    value: JsonValue,
    schema: dict[str, JsonValue],
    *,
    path: str,
) -> None:
    expected_type = schema.get("type")
    if expected_type is not None:
        allowed_types = _schema_types(expected_type, path=path)
        if not any(_matches_json_type(value, item) for item in allowed_types):
            joined = ", ".join(allowed_types)
            raise AgentResultContractError(
                f"{path} must match expected JSON type: {joined}"
            )

    enum_values = schema.get("enum")
    if enum_values is not None:
        if not isinstance(enum_values, list):
            raise AgentResultContractError(
                f"Invalid expected_output_schema at {path}: enum must be an array"
            )
        if value not in enum_values:
            raise AgentResultContractError(f"{path} is not one of the allowed values")

    if isinstance(value, dict):
        _validate_object(value, schema, path=path)
    elif isinstance(value, list):
        _validate_array(value, schema, path=path)
    elif isinstance(value, str):
        _validate_string(value, schema, path=path)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        _validate_number(value, schema, path=path)


def _schema_types(value: JsonValue, *, path: str) -> list[str]:
    if isinstance(value, str):
        types = [value]
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        types = [cast(str, item) for item in value]
    else:
        raise AgentResultContractError(
            f"Invalid expected_output_schema at {path}: type must be a string "
            "or an array of strings"
        )
    if not types:
        raise AgentResultContractError(
            f"Invalid expected_output_schema at {path}: type array cannot be empty"
        )
    supported = {"object", "array", "string", "boolean", "integer", "number", "null"}
    unsupported = [item for item in types if item not in supported]
    if unsupported:
        raise AgentResultContractError(
            f"Invalid expected_output_schema at {path}: unsupported type "
            f"{unsupported[0]!r}"
        )
    return types


def _matches_json_type(value: JsonValue, expected_type: str) -> bool:
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return value is None


def _validate_object(
    value: dict[str, JsonValue],
    schema: dict[str, JsonValue],
    *,
    path: str,
) -> None:
    required = schema.get("required")
    if required is not None:
        if not isinstance(required, list) or not all(
            isinstance(item, str) for item in required
        ):
            raise AgentResultContractError(
                f"Invalid expected_output_schema at {path}: required must be "
                "an array of strings"
            )
        missing = [cast(str, item) for item in required if item not in value]
        if missing:
            raise AgentResultContractError(
                f"{path} is missing required fields: {', '.join(missing)}"
            )

    properties = schema.get("properties")
    if properties is None:
        return
    if not isinstance(properties, dict):
        raise AgentResultContractError(
            f"Invalid expected_output_schema at {path}: properties must be an object"
        )
    for name, child_schema in properties.items():
        if name not in value:
            continue
        if not isinstance(child_schema, dict):
            raise AgentResultContractError(
                f"Invalid expected_output_schema at {path}.{name}: property schema "
                "must be an object"
            )
        _validate_schema(value[name], child_schema, path=f"{path}.{name}")


def _validate_array(
    value: list[JsonValue],
    schema: dict[str, JsonValue],
    *,
    path: str,
) -> None:
    _validate_size(
        len(value),
        schema,
        path=path,
        minimum_key="minItems",
        maximum_key="maxItems",
        unit="items",
    )
    item_schema = schema.get("items")
    if item_schema is None:
        return
    if not isinstance(item_schema, dict):
        raise AgentResultContractError(
            f"Invalid expected_output_schema at {path}: items must be an object"
        )
    for index, item in enumerate(value):
        _validate_schema(item, item_schema, path=f"{path}[{index}]")


def _validate_string(
    value: str,
    schema: dict[str, JsonValue],
    *,
    path: str,
) -> None:
    _validate_size(
        len(value),
        schema,
        path=path,
        minimum_key="minLength",
        maximum_key="maxLength",
        unit="characters",
    )


def _validate_size(
    size: int,
    schema: dict[str, JsonValue],
    *,
    path: str,
    minimum_key: str,
    maximum_key: str,
    unit: str,
) -> None:
    minimum = _optional_nonnegative_integer(schema, minimum_key, path=path)
    maximum = _optional_nonnegative_integer(schema, maximum_key, path=path)
    if minimum is not None and size < minimum:
        raise AgentResultContractError(f"{path} must contain at least {minimum} {unit}")
    if maximum is not None and size > maximum:
        raise AgentResultContractError(f"{path} must contain at most {maximum} {unit}")


def _optional_nonnegative_integer(
    schema: dict[str, JsonValue], key: str, *, path: str
) -> int | None:
    value = schema.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise AgentResultContractError(
            f"Invalid expected_output_schema at {path}: {key} must be a "
            "non-negative integer"
        )
    return value


def _validate_number(
    value: int | float,
    schema: dict[str, JsonValue],
    *,
    path: str,
) -> None:
    minimum = _optional_number(schema, "minimum", path=path)
    maximum = _optional_number(schema, "maximum", path=path)
    if minimum is not None and value < minimum:
        raise AgentResultContractError(f"{path} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise AgentResultContractError(f"{path} must be at most {maximum}")


def _optional_number(
    schema: dict[str, JsonValue], key: str, *, path: str
) -> int | float | None:
    value = schema.get(key)
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise AgentResultContractError(
            f"Invalid expected_output_schema at {path}: {key} must be a number"
        )
    return value
