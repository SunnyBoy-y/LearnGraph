"""Declarative programming exercise contract. This service never executes code."""

CODE_TRAINER_SPEC = {
    "display_name": "编程判题训练器",
    "data_schema": {
        "type": "object", "additionalProperties": False, "required": ["title"],
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 500},
            "description": {"type": "string", "maxLength": 8000},
            "language": {"type": "string", "minLength": 1, "maxLength": 40, "pattern": "^[a-zA-Z0-9+#._-]+$"},
            "starter_code": {"type": "string", "maxLength": 16000},
            "stdin": {"type": "string", "maxLength": 4000},
            "time_limit_ms": {"type": "integer", "minimum": 100, "maximum": 5000},
            "tests": {"type": "array", "maxItems": 12, "items": {
                "type": "object", "additionalProperties": False,
                "required": ["name", "stdin", "expected_stdout"],
                "properties": {
                    "name": {"type": "string", "maxLength": 100},
                    "stdin": {"type": "string", "maxLength": 4000},
                    "expected_stdout": {"type": "string", "maxLength": 4000},
                },
            }},
        },
    },
    "example_data": {
        "title": "两数之和", "description": "读取两个整数，输出它们的和。",
        "language": "python", "starter_code": "a, b = map(int, input().split())\nprint(a + b)",
        "stdin": "2 3", "tests": [{"name": "正整数", "stdin": "2 3", "expected_stdout": "5"}],
    },
}
