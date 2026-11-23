"""Exercise both installed CLI commands outside the checkout with external work blocked."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from uuid import uuid4


CHILD = r'''
import importlib.abc
import json
import os
from pathlib import Path
import socket
import sys

target, destination, checkout = map(Path, sys.argv[1:])
outside = not Path.cwd().resolve().is_relative_to(checkout.resolve()) and not destination.resolve().is_relative_to(checkout.resolve())
assert outside, "wheel smoke must run outside the source checkout"
sys.path.insert(0, str(target))
import writscope
if not Path(writscope.__file__).resolve().is_relative_to(target.resolve()):
    raise AssertionError("package was imported from the checkout")

class NoResearchImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if (fullname == "scripts" or fullname.startswith("scripts.") or
                fullname.startswith("writscope.providers") or
                fullname == "writscope.evaluation" or fullname == "dotenv"):
            raise AssertionError("installed CLI imported a model or evaluation entry")

sys.meta_path.insert(0, NoResearchImports())
def audit(event, args):
    if event.startswith("socket.") or event == "subprocess.Popen":
        raise AssertionError("installed CLI attempted network or process work")
    if event == "open" and isinstance(args[0], (str, bytes)):
        path = os.fsdecode(args[0]).replace("\\", "/")
        if "evaluation_private" in path.split("/") or Path(path).name.startswith(".env"):
            raise AssertionError("installed CLI read a private input")
sys.addaudithook(audit)
original_getitem = os._Environ.__getitem__
def env_getitem(self, name):
    if any(word in str(name).upper() for word in ("API_KEY", "TOKEN", "SECRET")):
        raise AssertionError("installed CLI read a credential environment variable")
    return original_getitem(self, name)
os._Environ.__getitem__ = env_getitem

from writscope.cli import main
if main(["demo", "--output", str(destination)]) != 0:
    raise AssertionError("installed CLI failed")
data = json.loads((destination / "demo.json").read_text(encoding="utf8"))
actual = [(item["id"], item["assessment"]["impact"], item["assessment"]["authorization"],
           item["assessment"]["decision"]) for item in data["cases"]]
assert actual == [("c001", "breaking", "not_covered", "revise"),
                  ("c002", "breaking", "covered", "allow"),
                  ("c003", "breaking", "not_covered", "revise")]
assert data["model_calls"] == 0 and data["code_executions"] == 0

endpoint = "/v1/items"
for filename, field in (("before.json", "label"), ("after.json", "title")):
    schema = {"type": "object", "required": [field],
              "properties": {field: {"type": "string"}}}
    response = {"description": "OK", "content": {"application/json": {"schema": schema}}}
    contract = {"openapi": "3.1.0", "info": {"title": "Wheel smoke", "version": "1"},
                "paths": {endpoint: {"get": {"responses": {"200": response}}}}}
    Path(filename).write_text(json.dumps(contract), encoding="utf-8")

assert main(["review", "--before", "before.json", "--after", "after.json",
             "--method", "GET", "--path", endpoint, "--output", "review"]) == 0
review = json.loads(Path("review/review.json").read_bytes())
assert (review["impact"], review["authorization"], review["decision"]) == (
    "breaking", "not_covered", "revise")
assert [item["field"] for item in review["impacts"]] == ["label"]
assert Path("review/index.html").is_file() and Path("review/manifest.json").is_file()
print(json.dumps({"installed_package": str(target), "outside_checkout": outside,
                  "offline_cases": 3, "contract_reviews": 1, "status": "passed"}))
'''


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    target = Path(os.environ["WRITSCOPE_WHEEL_TARGET"]).resolve()
    if not (target / "writscope").is_dir():
        raise SystemExit("Install the wheel into WRITSCOPE_WHEEL_TARGET first.")
    base = Path(os.environ.get("WRITSCOPE_DEMO_SMOKE_OUTPUT", tempfile.gettempdir()))
    working = base / ("writscope-wheel-check-" + uuid4().hex[:12])
    checkout = Path(__file__).resolve().parents[1]
    if working.resolve().is_relative_to(checkout):
        raise SystemExit("WRITSCOPE_DEMO_SMOKE_OUTPUT must be outside the source checkout.")
    working.mkdir(parents=True, mode=0o777)
    destination = working / "demo"
    env = {name: os.environ[name] for name in ("SystemRoot", "WINDIR", "TEMP", "TMP", "PATH") if name in os.environ}
    env["PYTHONIOENCODING"] = "utf-8"
    env["DEEPSEEK_API_KEY"] = "demo-smoke-sentinel-do-not-read"
    done = subprocess.run([sys.executable, "-I", "-B", "-c", CHILD, str(target), str(destination), str(checkout)],
                          cwd=working, env=env, capture_output=True, encoding="utf8", timeout=60)
    # Only synthetic inputs and diagnostics are printed; no real credential is passed.
    print(done.stdout, end="")
    if done.returncode:
        print(done.stderr, end="", file=sys.stderr)
    return done.returncode


if __name__ == "__main__":
    raise SystemExit(main())
