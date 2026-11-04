"""Run only the reviewed fixed S4 P1 fixture copies; not a security sandbox."""

import argparse
import json
import runpy
from pathlib import Path

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient


def observe(snapshot: Path, checker: Path, app_kind: str):
    module = runpy.run_path(str(snapshot / "app.py"))
    app = module["app"]
    load_page = runpy.run_path(str(snapshot / "client.py"))["load_page_model"]
    check = runpy.run_path(str(checker))["check_response"]
    endpoint = {"profile": "/api/v1/profile", "account": "/api/v1/account"}[app_kind]
    with TestClient(app) as client:
        response = client.get(endpoint)
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
        result = {"http_status": response.status_code, "response": payload,
                  "old_contract_checks": check(response.status_code, payload),
                  "page": page, "page_requests": calls,
                  "business_routes": sorted(f"{method} {route.path}" for route in app.routes
                                            if isinstance(route, APIRoute) for method in route.methods)}
        if app_kind == "account":
            result["account_value_probes"] = {
                "enabled": module["status_for_account"](True),
                "disabled": module["status_for_account"](False),
            }
            result["old_consumer_enabled"] = payload.get("status") == "active"
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("checker", type=Path)
    parser.add_argument("app_kind", choices=["profile", "account"])
    args = parser.parse_args()
    print(json.dumps(observe(args.snapshot, args.checker, args.app_kind), ensure_ascii=False))
