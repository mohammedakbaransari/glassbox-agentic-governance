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

        if not payload:
            return False, "Payload cannot be empty."

        schema = SCHEMAS.get(decision_type.value, [])

        for rule in schema:
            fname = rule["field"]
            required = rule.get("required", False)
            expected_type = rule.get("type")
            min_val = rule.get("min")

            value = payload.get(fname)

            # Required field check
            if required and value is None:
                return False, f"Required field '{fname}' is missing from payload."

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
