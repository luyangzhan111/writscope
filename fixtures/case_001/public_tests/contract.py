"""Public checks available equally to future review methods."""


def check_response(status_code, payload):
    is_object = isinstance(payload, dict)
    return {
        "http_200": status_code == 200,
        "status_exists": is_object and "status" in payload,
        "exact_response": is_object
        and payload == {"status": "active"}
        and type(payload.get("status")) is str,
    }
