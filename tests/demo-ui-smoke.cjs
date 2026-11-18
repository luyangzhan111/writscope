"use strict";

// Executes the saved page's UI with a minimal DOM. This checks data binding and
// keyboard behavior only; it does not render the page or inspect visual layout.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const htmlPath = process.argv[2];
assert.ok(htmlPath, "Usage: node tests/demo-ui-smoke.cjs <generated-index.html>");
const html = fs.readFileSync(htmlPath, "utf8");
const dataMatch = html.match(/<script id="writscope-demo-data" type="application\/json">([\s\S]*?)<\/script>/);
assert.ok(dataMatch, "generated page must contain JSON data");
const data = JSON.parse(dataMatch[1]);
const scripts = [...html.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)];
const executable = scripts.filter((match) => !/type="application\/json"/.test(match[1]));
assert.equal(executable.length, 1, "exactly one inline UI script");

let focusedElement = null;
class Element {
  constructor(tagName = "div") {
    this.tagName = tagName;
    this.children = [];
    this.attributes = {};
    this.dataset = {};
    this.listeners = {};
    this.hidden = false;
    this.tabIndex = 0;
    this._text = "";
  }
  get textContent() { return this._text + this.children.map((child) => child.textContent).join(""); }
  set textContent(value) { this._text = String(value); this.children = []; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ""; this.children = children; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name]; }
  addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
  dispatch(name, event = {}) { for (const callback of this.listeners[name] || []) callback(event); }
  focus() { focusedElement = this; }
}

const elements = new Map();
for (const match of html.matchAll(/<([a-z][a-z0-9-]*)\b([^>]*)>/gi)) {
  const id = match[2].match(/\bid="([^"]+)"/);
  if (!id) continue;
  assert.ok(!elements.has(id[1]), `duplicate element ID: ${id[1]}`);
  const element = new Element(match[1]);
  element.hidden = /(?:^|\s)hidden(?:\s|$)/.test(match[2]);
  elements.set(id[1], element);
}
elements.get("writscope-demo-data").textContent = dataMatch[1];
const get = (id) => {
  assert.ok(elements.has(id), `missing DOM target: ${id}`);
  return elements.get(id);
};
const document = {
  title: "",
  getElementById: get,
  createElement: (tagName) => new Element(tagName),
  createTextNode: (content) => { const element = new Element("#text"); element.textContent = content; return element; },
};
vm.runInNewContext(executable[0][2], {document}, {filename: "generated-demo-app.js", timeout: 2000});

const tabs = get("case-tabs");
assert.equal(data.cases.length, 3);
assert.equal(tabs.children.length, 3);
assert.equal(get("load-error").hidden, true, get("load-error").textContent);
assert.equal(get("case-panel").hidden, false);
const expected = [
  ["c001", "未获覆盖", "需修改"],
  ["c002", "授权覆盖", "允许"],
  ["c003", "未获覆盖", "需修改"],
];
for (let index = 0; index < expected.length; index += 1) {
  tabs.children[index].dispatch("click");
  const [id, authorization, decision] = expected[index];
  assert.equal(get("case-id").textContent, id);
  assert.equal(get("impact-value").textContent, "破坏兼容");
  assert.equal(get("authorization-value").textContent, authorization);
  assert.equal(get("decision-value").textContent, decision);
  assert.equal(get("event-list").children.length, 5);
  assert.ok(get("constraint-list").textContent.includes("保留 status 字段"));
  assert.ok(!get("impact-list").textContent.includes("no_active_grant_"));
  assert.equal(tabs.children[index].getAttribute("aria-selected"), "true");
  assert.equal(tabs.children[index].tabIndex, 0);
  if (id === "c002") {
    assert.ok(get("authorization-list").textContent.includes("绑定本次候选 · 已匹配"));
    assert.ok(get("authorization-list").textContent.includes(data.cases[index].patch.sha256));
    assert.ok(!get("authorization-list").textContent.includes("unknown"));
    assert.ok(get("impact-list").textContent.includes("有效许可精确匹配"));
  }
}
function key(name, expectedIndex) {
  let prevented = false;
  tabs.dispatch("keydown", {key: name, preventDefault() { prevented = true; }});
  assert.equal(prevented, true, `${name} should prevent default scrolling`);
  assert.equal(get("case-id").textContent, expected[expectedIndex][0]);
  assert.equal(focusedElement, tabs.children[expectedIndex]);
}
key("Home", 0);
key("ArrowRight", 1);
key("End", 2);
key("ArrowRight", 0);
key("ArrowLeft", 2);
assert.equal(get("load-error").hidden, true);
console.log("Offline DOM smoke passed: three scenarios, data binding, candidate binding and keyboard selection. Visual layout was not inspected.");
