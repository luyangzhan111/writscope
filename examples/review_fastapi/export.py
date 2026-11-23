"""Execute the three owned variants in TestClient and export local evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
from importlib.metadata import version
import json
from pathlib import Path
import platform
from uuid import uuid4

from fastapi.testclient import TestClient

from .service import REQUEST_PATH, ROUTE, create_app, new_client, old_client


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def new_output_path(path: Path) -> Path:
    if str(path).startswith(("\\\\", "//")):
        raise ValueError("example output must use a local path")
    path = path.absolute()
    if any(part.lower() in {".git", ".venv", "venv"} for part in path.resolve().parts):
        raise ValueError("example output cannot be inside Git or a virtual environment")
    if any(item.is_symlink() or item.is_junction() for item in (path, *path.parents)):
        raise ValueError("example output cannot pass through a link")
    if path.exists():
        raise ValueError("output must be a new directory")
    return path


def _consumer_result(consumer, payload: dict) -> dict:
    try:
        return {"status": "succeeded", "result": consumer(payload)}
    except KeyError as error:
        return {"status": "failed", "error": "missing_response_field", "field": str(error.args[0])}


def _response_schema(openapi: dict) -> dict:
    schema = openapi["paths"][ROUTE]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    reference = schema.get("$ref")
    if not isinstance(reference, str) or not reference.startswith("#/components/schemas/"):
        raise ValueError("unexpected owned example schema layout")
    return openapi["components"]["schemas"][reference.rsplit("/", 1)[1]]


def export(destination: Path) -> dict:
    destination = new_output_path(destination)

    payloads: dict[str, bytes] = {}
    observations = {}
    for variant in ("before", "breaking", "fixed"):
        with TestClient(create_app(variant)) as client:
            schema_response = client.get("/openapi.json")
            schema_response.raise_for_status()
            openapi = schema_response.json()
            response = client.get(REQUEST_PATH)
            response.raise_for_status()
            body = response.json()
        schema = _response_schema(openapi)
        payloads[f"{variant}.openapi.json"] = json_bytes(openapi)
        observations[variant] = {
            "request": {"method": "GET", "path": REQUEST_PATH},
            "response": {"status_code": response.status_code, "json": body},
            "old_client": _consumer_result(old_client, body),
            "new_client": _consumer_result(new_client, body),
            "declared_response": {
                "required": schema["required"],
                "properties": sorted(schema["properties"]),
                "additional_properties": schema.get("additionalProperties"),
            },
        }

    expected = {
        "before": ("succeeded", "failed", {"order_id", "status"}),
        "breaking": ("failed", "succeeded", {"order_id", "state"}),
        "fixed": ("succeeded", "succeeded", {"order_id", "status", "state"}),
    }
    for variant, (old, new, keys) in expected.items():
        item = observations[variant]
        if (item["old_client"]["status"] != old or item["new_client"]["status"] != new
                or set(item["response"]["json"]) != keys
                or set(item["declared_response"]["required"]) != keys
                or item["declared_response"]["additional_properties"] is not True):
            raise ValueError("owned example observation disagrees with its declared contract")

    observation_record = {
        "schema_version": "writscope_owned_fastapi_evidence_v1",
        "execution_kind": "owned_example_testclient_execution",
        "production_data": False,
        "model_calls": 0,
        "external_requests": 0,
        "scope": "Three fixed application variants executed in TestClient; no arbitrary project code.",
        "old_contract_extra_fields": "allowed",
        "observations": observations,
    }
    payloads["observations.json"] = json_bytes(observation_record)
    files = {name: sha256(data).hexdigest() for name, data in payloads.items()}
    manifest = {
        "schema_version": "writscope_owned_fastapi_export_manifest_v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "execution_kind": observation_record["execution_kind"],
        "production_data": False,
        "model_calls": 0,
        "external_requests": 0,
        "python": platform.python_version(),
        "dependencies": {name: version(name) for name in ("fastapi", "pydantic", "httpx", "starlette")},
        "source_files": {name: sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                         for name in ("service.py", "export.py")},
        "files": files,
        "limitations": [
            "The example is authored for this project, not a production incident or user dataset.",
            "Runtime observations cover one fixed order response per application variant.",
            "OpenAPI describes declared response structure, not complete business semantics.",
            "The local consumer checks require fields and accept additional response properties.",
            "No natural-language authorization inference is performed by this export.",
        ],
    }
    payloads["manifest.json"] = json_bytes(manifest)
    destination.mkdir(parents=True)
    for name, data in payloads.items():
        with (destination / name).open("xb") as stream:
            stream.write(data)
    return {"output": str(destination), "files": list(payloads),
            "execution_kind": observation_record["execution_kind"], "model_calls": 0}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parent / ".work" / ("evidence-" + uuid4().hex[:12]))
    args = parser.parse_args(argv)
    try:
        result = export(args.output)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"Export failed: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
