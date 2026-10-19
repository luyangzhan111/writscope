"""Execute only the reviewed synthetic fixture; this is not a security sandbox."""

import argparse
import json
from pathlib import Path
import runpy

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient


def observe(snapshot: Path, checker: Path):
    app = runpy.run_path(str(snapshot / "app.py"))["app"]
    load_page = runpy.run_path(str(snapshot / "client.py"))["load_page_model"]
    check = runpy.run_path(str(checker))["check_response"]
    with TestClient(app) as client:
        response = client.get("/api/v1/profile")
        calls = []

        class PageClient:
            def get(self, path):
                calls.append({"method": "GET", "path": path})
                return client.get(path)

        try:
            model = load_page(PageClient())
            page = {"status": "succeeded", "model": model, "error": None}
        except KeyError as error:
            page = {"status": "failed", "model": None,
                    "error": {"type": "KeyError", "missing_key": error.args[0]}}
        payload = response.json()
        return {
            "http_status": response.status_code,
            "response": payload,
            "old_contract_checks": check(response.status_code, payload),
            "page": page,
            "page_requests": calls,
            "business_routes": sorted(
                f"{method} {route.path}"
                for route in app.routes if isinstance(route, APIRoute)
                for method in route.methods
            ),
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("checker", type=Path)
    args = parser.parse_args()
    print(json.dumps(observe(args.snapshot, args.checker), ensure_ascii=False))
