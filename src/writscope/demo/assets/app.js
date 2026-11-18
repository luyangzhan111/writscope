(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var labels = {
    compatible: "兼容", breaking: "破坏兼容", unknown: "影响未知",
    covered: "授权覆盖", not_covered: "未获覆盖", ambiguous: "授权含糊", not_needed: "无需授权",
    allow: "允许", revise: "需修改", ask: "需澄清", investigate: "需调查",
    active: "有效", revoked: "已撤销", superseded: "已替代",
    user: "用户", tool: "工具事实", assistant: "助手", system: "系统",
    field_exists: "字段存在性", value_semantics: "字段值语义", response_shape: "响应形状",
    add_alias: "新增别名", remove_field: "删除字段", replace_field: "替换字段", change_value_semantics: "改变值语义",
    add_constraint: "新增约束", grant: "授予许可", revoke: "撤销许可", supersede: "替代条目", mark_ambiguous: "标记歧义", no_change: "状态不变"
  };
  var axisDetails = {
    compatible: "在已评估的契约范围内兼容", breaking: "破坏事实不随授权变化", unknown: "需要补充实际影响证据",
    covered: "有效许可覆盖所有破坏影响", not_covered: "至少一项影响不在有效许可内", ambiguous: "授权范围存在实质歧义", not_needed: "兼容影响无需破坏许可",
    allow: "通过本次兼容性与授权检查", revise: "修改当前候选补丁", ask: "先澄清具体授权范围", investigate: "先补充验证影响"
  };
  var reasonLabels = {
    constraint_interpretation_ambiguous: "许可虽匹配，但相关约束的解释仍存在歧义。",
    exact_active_grant_and_conditions: "有效许可精确匹配本次影响的范围与操作，且所需条件已满足。",
    grant_or_condition_ambiguous: "存在可能相关的许可，但许可解释或附加条件尚不能确认。",
    no_active_grant_covers_scope_operation_constraints_and_conditions: "没有有效许可同时覆盖本次影响的范围、操作、相关约束与条件。"
  };
  var data;
  var activeIndex = 0;

  function str(value) {
    if (value === undefined || value === null) return "";
    return typeof value === "string" ? value : JSON.stringify(value);
  }

  function translated(value) { return labels[value] || str(value); }
  function list(value) { return Array.isArray(value) ? value : []; }
  function text(id, value) { $(id).textContent = str(value); }
  function clear(node) { node.replaceChildren(); }

  function node(tag, className, value) {
    var el = document.createElement(tag);
    if (className) el.className = className;
    if (value !== undefined) el.textContent = str(value);
    return el;
  }

  function tone(value) {
    if (value === "covered" || value === "allow" || value === "compatible" || value === "not_needed") return "positive";
    if (value === "ambiguous" || value === "ask" || value === "investigate" || value === "unknown") return "uncertain";
    return "negative";
  }

  function sourceQuote(ref, parent) {
    if (!ref || (!ref.event_id && !ref.quote)) return;
    var quote = node("blockquote", "source-quote");
    if (ref.event_id) quote.append(node("span", "source-ref", ref.event_id + " · "));
    quote.append(document.createTextNode(str(ref.quote)));
    parent.append(quote);
  }

  function subjects(items) {
    return list(items).map(function (item) {
      return [item.method, item.path, item.field ? "· " + item.field : ""].filter(Boolean).join(" ");
    }).join("；");
  }

  function sourceDisclosure(item, parent) {
    var details = node("details", "disclosure");
    details.append(node("summary", "", "查看来源原话"));
    sourceQuote({event_id: item.source_event_id, quote: item.source_quote}, details);
    parent.append(details);
  }

  function renderEvents(current) {
    var target = $("event-list");
    clear(target);
    var events = list(current.events);
    text("event-count", events.length + " 条事件");
    events.forEach(function (event, index) {
      var item = node("li", "event-item" + (index === events.length - 1 ? " is-decision" : ""));
      var meta = node("div", "event-meta");
      meta.append(node("span", "event-id", event.event_id));
      meta.append(node("span", "role-label " + (event.role === "user" ? "user" : ""), translated(event.role)));
      if (index === events.length - 1) meta.append(node("span", "decision-tag", "决策时点"));
      item.append(meta, node("p", "event-content", event.content));
      target.append(item);
    });
  }

  function ledgerItem(item, authorization, current) {
    var article = node("article", "ledger-item");
    var header = node("div", "ledger-header");
    header.append(node("code", "ledger-id", authorization ? item.authorization_id : item.constraint_id));
    header.append(node("span", "lifecycle" + (item.lifecycle !== "active" ? " inactive" : ""), translated(item.lifecycle)));
    article.append(header, node("p", "subject-line", subjects(item.subjects)));
    if (authorization) {
      var change = item.change_details || {};
      var operation = translated(item.change_type);
      if (change.old_field || change.new_field) operation += "：" + str(change.old_field) + " → " + str(change.new_field);
      article.append(node("p", "predicate-line", operation));
      list(item.conditions).forEach(function (condition) {
        if (condition.kind === "candidate_sha256") {
          var patchHash = str((current.patch || {}).sha256);
          var matched = Boolean(patchHash) && str(condition.value) === patchHash;
          article.append(node("p", "ledger-detail", "绑定本次候选 · " + (matched ? "已匹配" : "未匹配")));
          var hashDetails = node("details", "disclosure");
          hashDetails.append(node("summary", "", "查看候选绑定哈希"));
          hashDetails.append(node("span", "meta-label", "许可条件 SHA-256"));
          hashDetails.append(node("code", "hash", condition.value));
          hashDetails.append(node("span", "meta-label", "当前候选 SHA-256"));
          hashDetails.append(node("code", "hash", patchHash || "未提供"));
          article.append(hashDetails);
        } else {
          var resolution = {satisfied: "已满足", unsatisfied: "未满足", unknown: "未确认"}[condition.resolution] || str(condition.resolution);
          article.append(node("p", "ledger-detail", "条件 · " + str(condition.kind) + " = " + str(condition.value) + (resolution ? " · " + resolution : "")));
        }
      });
    } else {
      var predicate = item.predicate || {};
      var fields = Array.from(new Set(list(item.subjects).map(function (subject) { return subject.field; }).filter(Boolean)));
      var predicateText = predicate.kind === "field_exists" && predicate.expected === true && fields.length
        ? "保留 " + fields.join("、") + " 字段"
        : translated(predicate.kind) + " · " + str(predicate.expected);
      article.append(node("p", "predicate-line", predicateText));
    }
    sourceDisclosure(item, article);
    return article;
  }

  function renderState(current) {
    var state = current.state || {};
    var constraints = list(state.constraints);
    var authorizations = list(state.authorizations);
    var constraintsTarget = $("constraint-list");
    var authorizationsTarget = $("authorization-list");
    clear(constraintsTarget);
    clear(authorizationsTarget);
    text("constraint-count", constraints.length + " 条");
    text("authorization-count", authorizations.length + " 条");
    constraints.forEach(function (item) { constraintsTarget.append(ledgerItem(item, false, current)); });
    authorizations.forEach(function (item) { authorizationsTarget.append(ledgerItem(item, true, current)); });
    if (!constraints.length) constraintsTarget.append(node("p", "empty-state", "当前没有约束条目。"));
    if (!authorizations.length) authorizationsTarget.append(node("p", "empty-state", "当前没有用户破坏性变更许可。"));

    var exceptionsTarget = $("exceptions-area");
    clear(exceptionsTarget);
    list(state.exceptions).forEach(function (exception) {
      exceptionsTarget.append(node("p", "exception-note", "局部例外 · " + str(exception.authorization_id) + " → " + list(exception.constraint_ids).join("、") + "。原约束保留，是否覆盖由当前有效许可及范围判断。"));
    });

    var contextTarget = $("context-list");
    clear(contextTarget);
    list(current.context).forEach(function (entry) {
      var item = node("div", "context-item");
      item.append(node("p", "", entry.requirement));
      sourceQuote({event_id: entry.source_event_id, quote: entry.quote}, item);
      contextTarget.append(item);
    });
    if (!list(current.context).length) contextTarget.append(node("p", "empty-state", "本案例无额外背景说明。"));
    text("state-hash", state.state_hash || "未提供");
  }

  function renderMatching(current) {
    var target = $("impact-list");
    clear(target);
    list((current.assessment || {}).per_impact).forEach(function (impact) {
      var item = node("article", "impact-item");
      var side = node("div", "");
      side.append(node("p", "impact-item-id", impact.impact_id));
      var verdict = node("span", "coverage-label", translated(impact.verdict));
      verdict.dataset.tone = tone(impact.verdict);
      side.append(verdict);
      var body = node("div", "");
      body.append(node("p", "impact-reason", reasonLabels[impact.reason] || impact.reason));
      var refs = [];
      if (list(impact.constraint_refs).length) refs.push("约束 · " + impact.constraint_refs.join("、"));
      if (list(impact.authorization_refs).length) refs.push("许可 · " + impact.authorization_refs.join("、"));
      if (refs.length) body.append(node("p", "reference-row", refs.join(" ｜ ")));
      list(impact.source_refs).forEach(function (ref) { sourceQuote(ref, body); });
      item.append(side, body);
      target.append(item);
    });
    if (!target.children.length) target.append(node("p", "empty-state", "未提供逐影响匹配记录。"));
  }

  function renderTransitions(current) {
    var target = $("transition-list");
    clear(target);
    list(current.transitions).forEach(function (transition) {
      var item = node("li", "");
      item.append(node("span", "transition-meta", transition.source_event_id));
      var body = node("div", "");
      body.append(node("p", "transition-operation", translated(transition.operation) + " · " + (transition.accepted ? "已接受" : "已拒绝")));
      if (transition.explanation) body.append(node("p", "transition-explanation", transition.explanation));
      item.append(body);
      target.append(item);
    });
  }

  function renderCase(index, moveFocus) {
    activeIndex = index;
    var current = data.cases[index];
    var buttons = $("case-tabs").children;
    Array.from(buttons).forEach(function (button, position) {
      button.setAttribute("aria-selected", position === index ? "true" : "false");
      button.tabIndex = position === index ? 0 : -1;
    });
    $("case-panel").setAttribute("aria-labelledby", "case-tab-" + index);
    if (moveFocus) buttons[index].focus();
    text("case-id", current.id);
    text("case-title", current.title);
    text("case-summary", current.summary);
    text("case-explanation", current.explanation);
    var assessment = current.assessment || {};
    ["impact", "authorization", "decision"].forEach(function (axis) {
      text(axis + "-value", translated(assessment[axis]));
      text(axis + "-detail", axisDetails[assessment[axis]] || "");
    });
    $("decision-cell").dataset.tone = tone(assessment.decision);
    var patch = current.patch || {};
    text("patch-file", patch.file);
    text("patch-before", patch.before);
    text("patch-after", patch.after);
    text("patch-diff", patch.diff);
    renderEvents(current);
    renderState(current);
    renderMatching(current);
    renderTransitions(current);
    text("result-announcement", current.title + "。实际影响：" + translated(assessment.impact) + "；有效授权：" + translated(assessment.authorization) + "；最终决定：" + translated(assessment.decision) + "。");
  }

  function init() {
    var dataNode = $("writscope-demo-data");
    if (!dataNode) throw new Error("缺少内嵌演示数据，请使用项目演示生成器生成完整页面。");
    data = JSON.parse(dataNode.textContent);
    if (data.schema_version !== "writscope_offline_demo_v1" || !Array.isArray(data.cases) || !data.cases.length) throw new Error("演示数据版本或案例列表无效。");
    if (data.execution_kind !== "offline_scripted" || data.model_calls !== 0 || data.real_transport_allowed !== false) throw new Error("运行边界不符合离线预置演示要求。");
    document.title = str(data.title || "WritScope · 离线授权复核演示");
    if (data.title) text("page-title", data.title);
    if (data.subtitle) text("page-subtitle", data.subtitle);
    if (data.disclaimer) text("demo-disclaimer", data.disclaimer);
    text("model-calls", "模型请求 " + data.model_calls);
    var origin = data.impact_origin || {};
    text("impact-origin", origin.description);
    text("impact-source", origin.source);
    text("impact-hash", origin.sha256);
    var tabs = $("case-tabs");
    clear(tabs);
    data.cases.forEach(function (current, index) {
      var button = node("button", "case-tab");
      button.type = "button";
      button.id = "case-tab-" + index;
      button.setAttribute("role", "tab");
      button.setAttribute("aria-controls", "case-panel");
      button.setAttribute("aria-selected", index === 0 ? "true" : "false");
      button.tabIndex = index === 0 ? 0 : -1;
      button.append(node("span", "case-tab-label", "场景 " + String(index + 1).padStart(2, "0") + " · " + current.id));
      button.append(node("span", "case-tab-title", current.title));
      button.append(node("span", "case-tab-summary", current.summary));
      button.addEventListener("click", function () { renderCase(index, false); });
      tabs.append(button);
    });
    tabs.addEventListener("keydown", function (event) {
      var next = activeIndex;
      if (event.key === "ArrowRight") next = (activeIndex + 1) % data.cases.length;
      else if (event.key === "ArrowLeft") next = (activeIndex - 1 + data.cases.length) % data.cases.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = data.cases.length - 1;
      else return;
      event.preventDefault();
      renderCase(next, true);
    });
    renderCase(0, false);
  }

  try { init(); }
  catch (error) {
    $("case-panel").hidden = true;
    $("case-tabs").hidden = true;
    $("load-error").hidden = false;
    text("load-error", "无法载入演示：" + (error && error.message ? error.message : "数据错误"));
  }
}());
