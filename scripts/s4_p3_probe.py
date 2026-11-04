"""Execute reviewed P3 fixture copies only; this is not an OS sandbox."""

import argparse
import inspect
import json
from pathlib import Path
import runpy

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient


def observe(snapshot: Path, checker: Path, routes: list[str]):
    module = runpy.run_path(str(snapshot / "app.py"))
    client_module = runpy.run_path(str(snapshot / "client.py"))
    check = runpy.run_path(str(checker))["check_response"]
    app = module["app"]
    with TestClient(app) as client:
        responses = {}
        for route in routes:
            response = client.get(route)
            payload = response.json()
            responses[route] = {"http_status": response.status_code, "response": payload,
                                "old_contract_checks": check(response.status_code, payload)}
        calls = []

        class PageClient:
            def get(self, path):
                calls.append({"method": "GET", "path": path})
                return client.get(path)

        try:
            model = client_module["load_page_model"](PageClient())
            page = {"status": "succeeded", "model": model, "error": None}
        except KeyError as error:
            page = {"status": "failed", "model": None,
                    "error": {"type": "KeyError", "missing_key": error.args[0]}}
        observation = {"routes": responses, "page": page, "page_requests": calls,
                       "business_routes": sorted(f"{method} {route.path}" for route in app.routes
                                                 if isinstance(route, APIRoute) for method in route.methods)}
        if "status_for_account" in module:
            fn = module["status_for_account"]
            count = len(inspect.signature(fn).parameters)
            if count == 1:
                observation["value_probes"] = {"enabled": fn(True), "disabled": fn(False)}
            elif count == 2:
                observation["value_probes"] = {f"enabled_{int(enabled)}_session_{int(active)}": fn(enabled, active)
                                               for enabled in (False, True) for active in (False, True)}
            else:
                raise ValueError("unexpected fixed probe signature")
        return observation


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("checker", type=Path)
    parser.add_argument("routes", nargs="+")
    args = parser.parse_args()
    print(json.dumps(observe(args.snapshot, args.checker, args.routes), ensure_ascii=False))
