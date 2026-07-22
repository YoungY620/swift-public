const state = {
  brief: null,
  filter: "ALL",
};

const elements = {
  title: document.querySelector("#brief-title"),
  asOf: document.querySelector("#as-of"),
  select: document.querySelector("#brief-select"),
  actionCount: document.querySelector("#action-count"),
  buyCount: document.querySelector("#buy-count"),
  sellCount: document.querySelector("#sell-count"),
  status: document.querySelector("#status"),
  actions: document.querySelector("#actions"),
  template: document.querySelector("#action-template"),
};

const labels = {
  BUY: "买入",
  ADD: "加仓",
  SELL: "卖出",
  HIGH: "高置信",
  MEDIUM: "中置信",
  LOW: "低置信",
};

const treeLabels = {
  estimate: "点估计",
  partition: "情景分解",
  bayes_update: "贝叶斯更新",
  complement: "补集",
};

const treeFormulas = {
  estimate: "直接估计",
  partition: "Σ 分支权重 × 条件概率",
  bayes_update: "P(H|E) ∝ P(H) × P(E|H)",
  complement: "1 − 子节点概率",
};

function percent(value, signed = false) {
  const formatted = new Intl.NumberFormat("zh-CN", {
    style: "percent",
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
    signDisplay: signed ? "always" : "auto",
  });
  return formatted.format(value);
}

function localTime(value) {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function validBrief(brief) {
  return brief && brief.schema_version === "1.1" && brief.title === "预测市场研究信号" && Array.isArray(brief.actions);
}

function safeHttpsUrl(value, expectedHost = null) {
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== "https:") return null;
    if (expectedHost && !expectedHost.includes(parsed.hostname)) return null;
    return parsed.href;
  } catch {
    return null;
  }
}

function visibleActions() {
  if (!state.brief) return [];
  if (state.filter === "ENTRY") return state.brief.actions.filter((row) => row.action !== "SELL");
  if (state.filter === "SELL") return state.brief.actions.filter((row) => row.action === "SELL");
  return state.brief.actions;
}

function makeEvidence(item, domId) {
  const row = document.createElement("li");
  row.id = domId;
  row.dataset.evidenceId = item.id;
  const link = document.createElement("a");
  link.href = safeHttpsUrl(item.source_url) || "#";
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = item.source_title;
  const content = document.createElement("p");
  content.textContent = item.source_content;
  const impact = document.createElement("p");
  impact.className = "impact";
  impact.textContent = `概率影响：${item.probability_impact}`;
  row.append(link, content, impact);
  return row;
}

function setSupportTab(card, name, focus = false) {
  const tabs = Array.from(card.querySelectorAll("[data-support-tab]"));
  const panels = Array.from(card.querySelectorAll("[data-support-panel]"));
  tabs.forEach((tab) => {
    const active = tab.dataset.supportTab === name;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
    if (active && focus) tab.focus();
  });
  panels.forEach((panel) => {
    const active = panel.dataset.supportPanel === name;
    panel.classList.toggle("is-active", active);
    panel.hidden = !active;
  });
}

function setupSupportTabs(card, cardIndex) {
  const tabs = Array.from(card.querySelectorAll("[data-support-tab]"));
  tabs.forEach((tab, tabIndex) => {
    const name = tab.dataset.supportTab;
    const panel = card.querySelector(`[data-support-panel="${name}"]`);
    tab.id = `support-${cardIndex}-tab-${name}`;
    panel.id = `support-${cardIndex}-panel-${name}`;
    tab.setAttribute("aria-controls", panel.id);
    panel.setAttribute("aria-labelledby", tab.id);
    tab.addEventListener("click", () => setSupportTab(card, name));
    tab.addEventListener("keydown", (event) => {
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
      event.preventDefault();
      const direction = event.key === 'ArrowRight' ? 1 : -1;
      const next = (tabIndex + direction + tabs.length) % tabs.length;
      setSupportTab(card, tabs[next].dataset.supportTab, true);
    });
  });
}

function makeEvidenceRefs(refs, evidenceMap, onEvidence) {
  const known = Array.isArray(refs) ? refs.filter((id) => evidenceMap.has(id)) : [];
  if (!known.length) return null;
  const group = document.createElement("div");
  group.className = "tree-evidence-refs";
  const label = document.createElement("span");
  label.textContent = "关联证据";
  group.append(label);
  known.forEach((id) => {
    const entry = evidenceMap.get(id);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "tree-evidence-ref";
    button.textContent = `E${String(entry.index).padStart(2, "0")}`;
    button.title = entry.item.source_title;
    button.setAttribute("aria-label", `查看证据 ${entry.index}：${entry.item.source_title}`);
    button.addEventListener("click", () => onEvidence(entry));
    group.append(button);
  });
  return group;
}

function makeProbabilityNode(node, evidenceMap, onEvidence, depth = 0, path = "1") {
  const group = document.createElement("div");
  group.className = "tree-group";
  group.dataset.depth = String(depth);
  group.dataset.path = path;

  const card = document.createElement("article");
  card.className = `tree-node is-${node.type}`;
  const head = document.createElement("div");
  head.className = "tree-node-head";
  const identity = document.createElement("div");
  identity.className = "tree-node-identity";
  const nodePath = document.createElement("span");
  nodePath.className = "tree-node-path";
  nodePath.textContent = `节点 ${path}`;
  const type = document.createElement("span");
  type.className = "tree-node-type";
  type.textContent = treeLabels[node.type] || node.type;
  identity.append(nodePath, type);
  const label = document.createElement("strong");
  label.className = "tree-node-label";
  label.textContent = node.label;
  const probability = document.createElement("data");
  probability.className = "tree-node-probability";
  probability.value = String(node.probability);
  probability.textContent = percent(node.probability);
  head.append(identity, label, probability);

  const description = document.createElement("p");
  description.className = "tree-node-description";
  description.textContent = node.description;
  const formula = document.createElement("code");
  formula.className = "tree-formula";
  formula.textContent = treeFormulas[node.type] || "概率节点";
  card.append(head, description, formula);
  const refs = makeEvidenceRefs(node.evidence_refs, evidenceMap, onEvidence);
  if (refs) card.append(refs);
  group.append(card);

  if (node.type === "partition") {
    const children = document.createElement("div");
    children.className = "tree-children";
    node.branches.forEach((branch, branchIndex) => {
      const branchPath = `${path}.${branchIndex + 1}`;
      const row = document.createElement("section");
      row.className = "tree-branch";
      const meta = document.createElement("div");
      meta.className = "tree-branch-meta";
      const branchNumber = document.createElement("span");
      branchNumber.className = "tree-branch-path";
      branchNumber.textContent = `分支 ${branchPath}`;
      const branchLabel = document.createElement("strong");
      branchLabel.textContent = branch.label;
      const weight = document.createElement("data");
      weight.value = String(branch.weight);
      weight.textContent = `权重 ${percent(branch.weight)}`;
      meta.append(branchNumber, branchLabel, weight);
      const branchRefs = makeEvidenceRefs(branch.evidence_refs, evidenceMap, onEvidence);
      if (branchRefs) meta.append(branchRefs);
      row.append(meta, makeProbabilityNode(branch.child, evidenceMap, onEvidence, depth + 1, branchPath));
      children.append(row);
    });
    group.append(children);
  }

  if (node.type === "complement") {
    const childPath = `${path}.1`;
    const child = document.createElement("div");
    child.className = "tree-unary";
    const relation = document.createElement("span");
    relation.className = "tree-relation";
    relation.textContent = `补集 ${childPath} · 1 − P`;
    child.append(relation, makeProbabilityNode(node.child, evidenceMap, onEvidence, depth + 1, childPath));
    group.append(child);
  }

  if (node.type === "bayes_update") {
    const priorPath = `${path}.1`;
    const sequence = document.createElement("div");
    sequence.className = "tree-bayes-sequence";
    const priorLabel = document.createElement("span");
    priorLabel.className = "tree-relation";
    priorLabel.textContent = `先验 ${priorPath}`;
    sequence.append(priorLabel, makeProbabilityNode(node.prior, evidenceMap, onEvidence, depth + 1, priorPath));
    const updates = document.createElement("div");
    updates.className = "tree-updates";
    node.updates.forEach((update, updateIndex) => {
      const item = document.createElement("article");
      item.className = "tree-update";
      const updateHead = document.createElement("div");
      updateHead.className = "tree-update-head";
      const order = document.createElement("span");
      order.className = "tree-update-path";
      order.textContent = `更新 ${path}.${updateIndex + 2}`;
      const updateLabel = document.createElement("strong");
      updateLabel.textContent = update.label;
      const posterior = document.createElement("data");
      posterior.value = String(update.posterior_probability);
      posterior.textContent = `后验 ${percent(update.posterior_probability)}`;
      updateHead.append(order, updateLabel, posterior);
      const likelihoods = document.createElement("p");
      likelihoods.textContent = `P(E|真) ${percent(update.likelihood_if_true)} · P(E|假) ${percent(update.likelihood_if_false)}`;
      item.append(updateHead, likelihoods);
      const updateRefs = makeEvidenceRefs(update.evidence_refs, evidenceMap, onEvidence);
      if (updateRefs) item.append(updateRefs);
      updates.append(item);
    });
    sequence.append(updates);
    group.append(sequence);
  }
  return group;
}

function makeProbabilityTree(action, evidenceMap, onEvidence) {
  const fragment = document.createDocumentFragment();
  const overview = document.createElement("div");
  overview.className = "tree-overview";
  const copy = document.createElement("div");
  const eyebrow = document.createElement("span");
  eyebrow.textContent = "MODEL TRACE";
  const title = document.createElement("strong");
  title.textContent = `${action.outcome} 公允概率 ${percent(action.probability_tree.root.probability)}`;
  const note = document.createElement("p");
  note.textContent = "展开每条计算路径；点击 E 编号可返回对应证据。";
  copy.append(eyebrow, title, note);
  const check = document.createElement("span");
  check.className = "tree-check";
  check.textContent = "ROOT = FAIR PRICE";
  overview.append(copy, check);
  fragment.append(overview, makeProbabilityNode(action.probability_tree.root, evidenceMap, onEvidence, 0, "1"));
  return fragment;
}

function makeCard(action, index) {
  const card = elements.template.content.firstElementChild.cloneNode(true);
  const side = action.action === "SELL" ? "SELL" : "ENTRY";
  card.dataset.side = side;
  card.style.animationDelay = `${Math.min(index * 45, 180)}ms`;
  card.querySelector(".action-badge").textContent = `${labels[action.action]} ${action.outcome}`;
  card.querySelector(".confidence").textContent = labels[action.confidence];
  card.querySelector(".market-name").textContent = action.market_name;
  card.querySelector(".reason").textContent = action.reason;
  const link = card.querySelector(".market-link");
  link.href = safeHttpsUrl(action.polymarket_url, ["polymarket.com", "www.polymarket.com"]) || "#";
  card.querySelector(".current-price").textContent = percent(action.current_price);
  card.querySelector(".fair-price").textContent = percent(action.fair_price);
  card.querySelector(".price-gap").textContent = percent(action.price_gap, true);

  const start = Math.min(action.current_price, action.fair_price) * 100;
  const width = Math.abs(action.fair_price - action.current_price) * 100;
  const span = card.querySelector(".rail-span");
  span.style.left = `${start}%`;
  span.style.width = `${width}%`;
  card.querySelector(".market-marker").style.left = `${action.current_price * 100}%`;
  card.querySelector(".agent-marker").style.left = `${action.fair_price * 100}%`;

  setupSupportTabs(card, index);
  card.querySelector(".evidence-count").textContent = String(action.evidence.length);
  card.querySelector(".support-evidence-count").textContent = `${action.evidence.length} 条`;
  const list = card.querySelector(".evidence-list");
  const evidenceMap = new Map();
  action.evidence.forEach((item, evidenceIndex) => {
    const entry = {
      index: evidenceIndex + 1,
      item,
      element: makeEvidence(item, `support-${index}-evidence-${evidenceIndex + 1}`),
    };
    evidenceMap.set(item.id, entry);
    list.append(entry.element);
  });
  const onEvidence = (entry) => {
    setSupportTab(card, "evidence");
    card.querySelectorAll(".evidence-list li.is-referenced").forEach((item) => item.classList.remove("is-referenced"));
    entry.element.classList.add("is-referenced");
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    entry.element.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "center" });
  };
  card.querySelector(".probability-tree").append(makeProbabilityTree(action, evidenceMap, onEvidence));
  return card;
}

function render() {
  const all = state.brief.actions;
  const buys = all.filter((row) => row.action !== "SELL").length;
  const sells = all.length - buys;
  elements.title.textContent = state.brief.title;
  if (state.brief.brief_id === "empty") {
    elements.asOf.textContent = "尚未发布";
    elements.asOf.removeAttribute("datetime");
  } else {
    elements.asOf.textContent = localTime(state.brief.as_of);
    elements.asOf.dateTime = state.brief.as_of;
  }
  elements.actionCount.textContent = String(all.length);
  elements.buyCount.textContent = String(buys);
  elements.sellCount.textContent = String(sells);
  elements.status.textContent = "";
  elements.actions.replaceChildren();

  const visible = visibleActions();
  if (!visible.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    const title = document.createElement("strong");
    title.textContent = all.length ? "此筛选下没有操作" : "今日无新增交易信号";
    const note = document.createElement("span");
    note.textContent = all.length ? "切换筛选查看其他动作。" : "保持观察，等待价格与概率出现足够优势。";
    empty.append(title, note);
    elements.actions.append(empty);
    return;
  }
  visible.forEach((action, index) => elements.actions.append(makeCard(action, index)));
}

async function loadBrief(filename) {
  elements.status.className = "status";
  elements.status.textContent = "正在加载数据…";
  const response = await fetch(`data/${filename}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const brief = await response.json();
  if (!validBrief(brief)) throw new Error("公共简报格式不受支持");
  state.brief = brief;
  render();
}

async function initialize() {
  try {
    const response = await fetch("data/index.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const index = await response.json();
    const rows = Array.isArray(index.briefs) ? index.briefs : [];
    elements.select.replaceChildren();
    if (rows.length) {
      rows.forEach((row) => {
        const option = document.createElement("option");
        option.value = row.file;
        option.textContent = `${localTime(row.generated_at)} · ${row.title}`;
        elements.select.append(option);
      });
      elements.select.disabled = false;
      await loadBrief(rows[0].file);
    } else {
      const option = document.createElement("option");
      option.value = "latest.json";
      option.textContent = "暂无历史简报";
      elements.select.append(option);
      await loadBrief("latest.json");
    }
  } catch (error) {
    elements.status.className = "status is-error";
    elements.status.textContent = `无法读取公开简报：${error.message}`;
    elements.actions.replaceChildren();
  }
}

elements.select.addEventListener("change", () => loadBrief(elements.select.value).catch((error) => {
  elements.status.className = "status is-error";
  elements.status.textContent = `无法读取公开简报：${error.message}`;
}));

document.querySelectorAll(".filter").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".filter").forEach((item) => item.classList.remove("is-active"));
    button.classList.add("is-active");
    state.filter = button.dataset.filter;
    render();
  });
});

initialize();
