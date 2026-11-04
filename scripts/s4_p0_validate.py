"""Validate evaluator-only S4 design metadata; never construct model inputs."""

import argparse
import hashlib
import json
from pathlib import Path

from writscope.events import ConversationEvent, JudgmentLabels

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "evaluation_private/s4_p0/case_matrix_v1.json"


def validate(data, root=ROOT):
    def require(value, message):
        if not value:
            raise ValueError(message)

    require(data.get("status") == "author_draft_not_independently_reviewed", "incorrect status")
    require(data.get("execution") == "not_run", "design must not claim execution")
    pairs, controls = data["pairs"], data["controls"]
    require(len(pairs) == 12 and len(controls) == 6, "expected 12 pairs and 6 controls")
    require(sum(c["expected"]["authorization"] == "ambiguous" for c in controls) == 2,
            "expected two ambiguous controls")
    variables = {"explicit", "version", "operation", "subject", "lifecycle", "pressure"}
    require({(p["app"], p["variable"]) for p in pairs} ==
            {(app, variable) for app in ("A", "B") for variable in variables}, "pair coverage")
    all_ids = []
    for pair in pairs:
        require(len(pair["cases"]) == 2, "pair needs two cases")
        require(all(pair.get(k) for k in ("common_history", "candidate", "effect_plan", "variable_note")), "missing pair evidence")
        require(pair["cases"][0]["e005"] != pair["cases"][1]["e005"], "pair must vary")
        require({c["expected"]["authorization"] for c in pair["cases"]} == {"covered", "not_covered"}, "pair outcomes")
        for case in pair["cases"]:
            all_ids.append(case["case_id"])
            JudgmentLabels.model_validate(case["expected"])
            require(case["expected"]["impact"] == "breaking", "paired impact")
            require(case.get("rationale") and case.get("source_quote") and
                    case["source_quote"] in case["e005"], "missing user quote")
            require(case["event"]["seq"] == 5 and case["event"]["event_id"] == "e005" and
                    case["event"]["role"] == "user", "invalid decision event")
            for event in pair["common_history"] + [dict(case["event"], content=case["e005"])]:
                ConversationEvent.model_validate(event)
            require([x["seq"] for x in pair["common_history"]] == [1, 2, 3, 4], "event sequence")
    for case in controls:
        all_ids.append(case["case_id"])
        JudgmentLabels.model_validate(case["expected"])
        require(all(case.get(k) for k in ("history", "candidate", "effect_plan", "rationale")), "missing control evidence")
        for event in case["history"]:
            ConversationEvent.model_validate(event)
    require(len(set(all_ids)) == 30 and set(all_ids) == {f"s4_{i:03d}" for i in range(1, 31)}, "case ids")
    require({seed["path"] for seed in data["seeds"]} ==
            {f"fixtures/case_001/inputs/c00{i}.jsonl" for i in range(1, 4)}, "seed coverage")
    for seed in data["seeds"]:
        path = root / seed["path"]
        require(hashlib.sha256(path.read_bytes()).hexdigest() == seed["sha256"], "seed changed")
    seed_pair = next(p for p in pairs if p["app"] == "A" and p["variable"] == "explicit")
    for case in seed_pair["cases"]:
        events = [json.loads(line) for line in (root / case["seed_path"]).read_text(encoding="utf-8").splitlines()]
        require(events[:4] == seed_pair["common_history"] and events[4]["content"] == case["e005"], "seed not preserved")
    return {"pairs": len(pairs), "cases": len(all_ids), "controls": len(controls), "execution": "not_run", "semantic_review": "pending"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT)
    args = parser.parse_args()
    print(json.dumps(validate(json.loads(args.path.read_text(encoding="utf-8"))), ensure_ascii=True))


if __name__ == "__main__":
    main()
