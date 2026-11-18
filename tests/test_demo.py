"""Checks for the installable, offline demonstration, not model performance."""

from hashlib import sha256
from html.parser import HTMLParser
from collections import UserDict
import json
from pathlib import Path
import socket
import subprocess
from uuid import uuid4

import pytest

from writscope.cli import main
from writscope.demo import CONFIG_HASH, build_demo, generate, render_html
from writscope.events import ConversationEvent
from writscope.state import StateEngine, TransitionProposal, assess_impacts, verify_state_hash
from writscope.contracts import ImpactEvidence
from importlib.resources import files


@pytest.fixture
def tmp_path():
    # Python 3.12.4's Windows mode-0700 ACL is incompatible with this workspace.
    # Keep test-only outputs in a fresh ignored directory using ordinary ACLs.
    path = Path(__file__).resolve().parents[1] / ".work" / ("demo-test-" + uuid4().hex)
    path.mkdir(parents=True, mode=0o777)
    return path


def test_presets_use_original_public_bytes_and_recompute_coverage():
    data = build_demo()
    root = Path(__file__).resolve().parents[1]
    resources = files("writscope.demo").joinpath("data")
    for name, entry in data["provenance"]["files"].items():
        assert resources.joinpath(name).read_bytes() == (root / entry["source"]).read_bytes()
        assert entry["source"].startswith(("fixtures/", "reports/m1-"))
        assert "evaluation_private" not in entry["source"]
    evidence = ImpactEvidence.model_validate(json.loads(resources.joinpath("impact.json").read_bytes()))
    expected = {"c001": ("not_covered", "revise"), "c002": ("covered", "allow"),
                "c003": ("not_covered", "revise")}
    for case in data["cases"]:
        events = [ConversationEvent.model_validate(event) for event in case["events"]]
        for context in case["context"]:
            source = next(event for event in events if event.event_id == context["source_event_id"])
            assert source.role == "user" and source.content.count(context["quote"]) == 1
        engine = StateEngine(case["id"], events, CONFIG_HASH,
                             {"contract", "patch", "observed_original", "observed_candidate"})
        for entry in case["transitions"]:
            replayed = engine.submit(TransitionProposal.model_validate(entry["proposal"]))
            assert replayed.transition.model_dump() == {key: entry[key] for key in replayed.transition.model_dump()}
        state = engine.snapshot()
        verify_state_hash(state)
        assert state.model_dump() == case["state"]
        result = assess_impacts(state, evidence, case["patch"]["sha256"])
        assert result.model_dump() == case["assessment"]
        assert result.impact == "breaking"
        assert (result.authorization, result.decision) == expected[case["id"]]
        assert state.constraints[0].lifecycle == "active"
        if case["id"] == "c002":
            assert state.exceptions[0].constraint_ids == ["con_v1_status"]
            # Changing the actual candidate cannot reuse a "this candidate" grant.
            changed = evidence.model_copy(update={"patch_sha256": "0" * 64})
            assert assess_impacts(state, changed, "0" * 64).authorization == "not_covered"
        if case["id"] == "c003":
            assert not state.exceptions
            assert state.authorizations[0].subjects[0].api_version == "v2"
    assert build_demo() == data


def test_default_path_is_offline_and_never_opens_browser_or_reads_key(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("offline demo attempted external work or read a credential")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    import os
    class SyntheticEnvironment(UserDict):
        def __getitem__(self, name):
            if any(word in str(name).upper() for word in ("API_KEY", "TOKEN", "SECRET")):
                forbidden()
            return super().__getitem__(name)
    # Replace the mapping without enumerating or saving any real credential.
    monkeypatch.setattr(os, "environ", SyntheticEnvironment(
        {"DEEPSEEK_API_KEY": "not-a-real-key"}))
    original_open = Path.open
    def public_only(path, *args, **kwargs):
        assert "evaluation_private" not in path.parts
        assert not path.name.startswith(".env")
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", public_only)
    monkeypatch.chdir(tmp_path)
    assert main(["demo"]) == 0
    outputs = list((tmp_path / ".work").glob("writscope-demo-*"))
    assert len(outputs) == 1
    assert set(path.name for path in outputs[0].iterdir()) == {"index.html", "demo.json", "manifest.json"}
    for path in outputs[0].iterdir():
        assert "not-a-real-key" not in path.read_text(encoding="utf-8")


def test_manifest_no_overwrite_and_module_entry(tmp_path, capsys):
    destination = tmp_path / "demo"
    assert main(["demo", "--output", str(destination)]) == 0
    before = {path.name: path.read_bytes() for path in destination.iterdir()}
    manifest = json.loads(before["manifest.json"])
    assert manifest["execution_kind"] == "offline_scripted"
    assert manifest["reference_labels_read"] is False
    assert manifest["model_calls"] == manifest["code_executions"] == 0
    assert manifest["real_transport_allowed"] is False
    for name, expected in manifest["files"].items():
        assert sha256(before[name]).hexdigest() == expected
    assert main(["demo", "--output", str(destination)]) == 1
    assert {path.name: path.read_bytes() for path in destination.iterdir()} == before
    assert "demo.json" in capsys.readouterr().out


def test_open_is_explicit_and_uses_only_generated_file(tmp_path, monkeypatch):
    import webbrowser
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
    destination = tmp_path / "demo"
    assert main(["demo", "--output", str(destination), "--open"]) == 0
    assert opened == [(destination / "index.html").as_uri()]


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.data_script = False
        self.embedded = []
    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        self.tags.append((tag, values))
        if tag == "script" and values.get("id") == "writscope-demo-data":
            self.data_script = True
    def handle_endtag(self, tag):
        if tag == "script":
            self.data_script = False
    def handle_data(self, data):
        if self.data_script:
            self.embedded.append(data)


def test_self_contained_html_escapes_event_text_and_has_no_external_assets():
    data = build_demo()
    malicious = '</script><img src="https://example.invalid/leak">&\u2028'
    data["cases"][0]["events"][0]["content"] = malicious
    document = render_html(data)
    parser = PageParser()
    parser.feed(document)
    embedded = json.loads("".join(parser.embedded))
    assert embedded["cases"][0]["events"][0]["content"] == malicious
    assert not any(tag == "img" for tag, _ in parser.tags)
    assert not any("src" in attrs or ("href" in attrs and not attrs["href"].startswith("#"))
                   for _, attrs in parser.tags)
    assert not any(tag == "script" and "src" in attrs for tag, attrs in parser.tags)
    assert "connect-src 'none'" in document
    assert "WRITSCOPE_DEMO_" not in document


def test_rejects_git_internal_output(tmp_path):
    with pytest.raises(ValueError, match="Git internals"):
        generate(tmp_path / ".git" / "demo")
