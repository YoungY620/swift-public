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

function makeEvidence(item) {
  const row = document.createElement("li");
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

  card.querySelector(".evidence-count").textContent = String(action.evidence.length);
  const list = card.querySelector(".evidence-list");
  action.evidence.forEach((item) => list.append(makeEvidence(item)));
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
