"""
GlassBox Framework - Schema Validator
Validates incoming decision payloads against required field schemas per decision type.
No external dependencies.

Author: Mohammed Akbar Ansari
"""

from typing import Any, Dict, List, Optional, Tuple
from glassbox.governance.models import DecisionType


# Required fields and their expected types per decision type
SCHEMAS: Dict[str, List[Dict[str, Any]]] = {
    DecisionType.PROCUREMENT.value: [
        {"field": "amount",      "type": (int, float), "required": True,  "min": 0},
        {"field": "supplier_id", "type": str,           "required": False},
        {"field": "category",    "type": str,           "required": False},
    ],
    DecisionType.PRICING.value: [
        {"field": "new_price",      "type": (int, float), "required": True,  "min": 0},
        {"field": "previous_price", "type": (int, float), "required": False, "min": 0},
        {"field": "product_id",     "type": str,           "required": False},
    ],
    DecisionType.FINANCIAL.value: [
        {"field": "amount",              "type": (int, float), "required": True, "min": 0},
        {"field": "destination_account", "type": str,           "required": False},
        {"field": "reference",           "type": str,           "required": False},
    ],
    DecisionType.INVENTORY.value: [
        {"field": "quantity",    "type": (int, float), "required": True, "min": 0},
        {"field": "product_id",  "type": str,           "required": False},
        {"field": "warehouse_id","type": str,           "required": False},
    ],
    DecisionType.LOGISTICS.value: [
        {"field": "origin",      "type": str, "required": True},
        {"field": "destination", "type": str, "required": True},
    ],
    DecisionType.IT_OPS.value: [
        {"field": "action",    "type": str, "required": True},
        {"field": "target",    "type": str, "required": True},
        {"field": "service_id","type": str, "required": False},
    ],
    DecisionType.HR.value: [
        {"field": "action",      "type": str, "required": True},
        {"field": "employee_id", "type": str, "required": False},
    ],
    DecisionType.CLINICAL.value: [
        {"field": "drug_name", "type": str, "required": True},
        {"any_of": ["dose_mg", "dosage_mg"], "type": (int, float), "required": True, "min": 0},
    ],
    DecisionType.TRADING.value: [
        {"field": "symbol", "type": str, "required": True},
        {"any_of": ["notional", "order_value", "quantity"], "type": (int, float), "required": True, "min": 0},
    ],
    DecisionType.CONTENT.value: [
        {"field": "content", "type": str, "required": True},
    ],
    DecisionType.LEGAL.value: [
        {"field": "action", "type": str, "required": True},
    ],
    DecisionType.CUSTOM.value: [
        # CUSTOM type accepts any payload - no schema validation
        # (Decision ID and timestamp are added by the pipeline, not required from user)
    ],
}


class SchemaValidator:
    """
    Validates decision payloads against per-type schemas.
    Returns (is_valid: bool, error_message: Optional[str]).
    """

    def validate(
        self,
        decision_type: DecisionType,
        payload: Dict[str, Any],
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate the payload for the given decision type.
        Returns (True, None) on success, (False, error_message) on failure.
        """
        if not isinstance(payload, dict):
            return False, "Payload must be a dictionary."

        if decision_type == DecisionType.CUSTOM:
            return True, None

        if not payload:
            return False, "Payload cannot be empty."

        schema = SCHEMAS.get(decision_type.value, [])

        for rule in schema:
            candidate_fields = rule.get("any_of") or [rule["field"]]
            field_label = " or ".join(f"'{field_name}'" for field_name in candidate_fields)
            required = rule.get("required", False)
            expected_type = rule.get("type")
            min_val = rule.get("min")

            fname = None
            value = None
            for field_name in candidate_fields:
                if payload.get(field_name) is not None:
                    fname = field_name
                    value = payload.get(field_name)
                    break

            # Required field check
            if required and value is None:
                if len(candidate_fields) == 1:
                    return False, f"Required field {field_label} is missing from payload."
                return False, f"One of fields {field_label} is required in payload."

            if value is not None:
                # Type check
                if expected_type and not isinstance(value, expected_type):
                    if isinstance(expected_type, tuple):
                        type_name = " or ".join(t.__name__ for t in expected_type)
                    elif hasattr(expected_type, "__name__"):
                        type_name = expected_type.__name__
                    else:
                        type_name = str(expected_type)
                    return False, (
                        f"Field '{fname}' must be of type {type_name}, "
                        f"got {type(value).__name__}."
                    )
                # Min value check
                if min_val is not None and isinstance(value, (int, float)):
                    if value < min_val:
                        return False, f"Field '{fname}' must be >= {min_val}, got {value}."

        return True, None
