const state = {
  snapshot: null,
  eventCursor: 0,
  events: [],
  notice: null,
  noticeUntil: 0,
  selected: null,
  seenThoughts: new Set(),
  thoughtBubbles: new Map(),
  activeThoughtKeys: new Set(),
};

const appNode = document.getElementById("app");
const statusLine = document.getElementById("status-line");
const campaignLineNode = document.getElementById("campaign-line");
const phaseActionsNode = document.getElementById("phase-actions");
const dayPulseNode = document.getElementById("day-pulse");
const prepCounterNode = document.getElementById("prep-counter");
const standingCustomersNode = document.getElementById("standing-customers");
const staffStageNode = document.getElementById("staff-stage");
const queueRiverNode = document.getElementById("queue-river");
const tablesMapNode = document.getElementById("tables-map");
const storageZoneNode = document.getElementById("storage-zone");
const registerZoneNode = document.getElementById("register-zone");
const historyWallNode = document.getElementById("history-wall");
const inspectorNode = document.getElementById("inspector");
const inspectorContentNode = document.getElementById("inspector-content");
const inspectorCloseBtn = document.getElementById("inspector-close");
const menuNode = document.getElementById("menu-list");
const eventLogNode = document.getElementById("event-log");
const spawnIntervalInput = document.getElementById("spawn-interval");
const simDurationInput = document.getElementById("sim-duration");
const stopBtn = document.getElementById("stop-btn");
const resetBtn = document.getElementById("reset-btn");
const saveSettingsBtn = document.getElementById("save-settings-btn");

const openOrderStatuses = new Set(["pending", "claimed", "preparing", "ready"]);
const activeOrderStatuses = ["pending", "claimed", "preparing", "ready"];
const pipelineLabels = {
  pending: "Waiting",
  claimed: "Claimed",
  preparing: "Making",
  ready: "Ready",
  delivered: "Picked up",
  abandoned: "Abandoned",
  stale: "Stale",
  failed: "Failed",
};

async function api(path, method = "GET", body = null) {
  const response = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || "Request failed.");
  }
  if (response.status === 204) {
    return null;
  }
  return response.json();
}

function clear(node) {
  node.replaceChildren();
}

function createElement(tag, className = null, text = null) {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text != null) {
    node.textContent = text;
  }
  return node;
}

function appendText(parent, tag, className, text) {
  const node = createElement(tag, className, text);
  parent.appendChild(node);
  return node;
}

function formatMoney(value) {
  return `$${Number(value || 0).toFixed(2)}`;
}

function formatItemList(items) {
  return (items || []).length ? items.join(", ") : "none";
}

function formatTime(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function clampText(text, maxLength = 96) {
  const clean = String(text || "").replace(/\s+/g, " ").trim();
  if (clean.length <= maxLength) {
    return clean;
  }
  return `${clean.slice(0, maxLength - 3).trim()}...`;
}

function extractBoldThought(summary) {
  const matches = [];
  const markdownBoldPattern = /(?:\*\*|__)([^*_][\s\S]*?[^*_]|[^*_])(?:\*\*|__)/g;
  let match = markdownBoldPattern.exec(summary || "");

  while (match) {
    const thought = match[1].replace(/[`*_]/g, "").replace(/\s+/g, " ").trim();
    if (thought) {
      matches.push(thought.replace(/:$/, ""));
    }
    match = markdownBoldPattern.exec(summary || "");
  }

  return clampText([...new Set(matches)].slice(0, 2).join(" / "), 82);
}

function getThinkingByAgent(snapshot) {
  return new Map((snapshot.agent_thinking || []).map((entry) => [entry.agent_id, entry]));
}

function getThoughtKey(kind, person) {
  const id = person.customer_id || person.id || person.agent_id || person.display_name || person.name;
  return id ? `${kind}:${id}` : "";
}

function clearThoughtBubbles() {
  state.thoughtBubbles.forEach((entry) => entry.node.remove());
  state.thoughtBubbles.clear();
  state.activeThoughtKeys.clear();
}

function upsertThoughtBubble(parent, summary, key) {
  const thought = extractBoldThought(summary);
  if (!key) {
    return null;
  }

  if (!thought) {
    const existing = state.thoughtBubbles.get(key);
    if (existing) {
      existing.node.remove();
      state.thoughtBubbles.delete(key);
    }
    return null;
  }

  const fingerprint = `${key}:${thought}`;
  const settled = state.seenThoughts.has(fingerprint);
  state.seenThoughts.add(fingerprint);
  state.activeThoughtKeys.add(key);

  const existing = state.thoughtBubbles.get(key);
  if (existing && existing.thought === thought) {
    existing.node.className = "thought-bubble is-settled";
    if (existing.node.parentNode !== parent) {
      parent.appendChild(existing.node);
    }
    return existing.node;
  }

  const bubble = existing?.node || createElement("div");
  bubble.className = `thought-bubble${settled ? " is-settled" : ""}`;
  bubble.textContent = thought;
  bubble.title = thought;
  parent.appendChild(bubble);
  state.thoughtBubbles.set(key, { node: bubble, thought });
  return bubble;
}

function beginThoughtRender() {
  state.activeThoughtKeys.clear();
}

function pruneThoughtBubbles() {
  state.thoughtBubbles.forEach((entry, key) => {
    if (!state.activeThoughtKeys.has(key)) {
      entry.node.remove();
      state.thoughtBubbles.delete(key);
    }
  });
}

function getMode(snapshot) {
  const sim = snapshot.simulation;
  const calendar = snapshot.calendar;
  if (calendar.phase === "settled") {
    return "history";
  }
  if (sim.running || sim.phase === "closing" || calendar.phase === "open") {
    return "live";
  }
  return "planning";
}

function selectItem(kind, id) {
  state.selected = { kind, id };
  renderInspector();
  markSelected();
}

function selectedKey() {
  return state.selected ? `${state.selected.kind}:${state.selected.id}` : "";
}

function selectable(node, kind, id) {
  node.dataset.selectKey = `${kind}:${id}`;
  node.tabIndex = 0;
  node.addEventListener("click", () => selectItem(kind, id));
  node.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      selectItem(kind, id);
    }
  });
  return node;
}

function markSelected() {
  document.querySelectorAll("[data-select-key]").forEach((node) => {
    node.classList.toggle("is-selected", node.dataset.selectKey === selectedKey());
  });
  inspectorNode.classList.toggle("is-open", Boolean(state.selected));
}

function setNotice(message, isError = false) {
  state.notice = { message, isError };
  state.noticeUntil = Date.now() + 3500;
  renderStatus();
}

function getOpenOrders(snapshot) {
  return (snapshot.queue || []).filter((order) => openOrderStatuses.has(order.status));
}

function getSupplyAlerts(snapshot) {
  return Object.entries(snapshot.supplies || {}).filter(([, supply]) => supply.status !== "normal");
}

function getCustomerOrder(snapshot, customerId) {
  return getOpenOrders(snapshot).find((order) => order.customer_id === customerId);
}

function getPerson(snapshot, id) {
  if ((snapshot.staff || {})[id]) {
    return { kind: "staff", id, ...snapshot.staff[id] };
  }
  return (snapshot.active_customers || []).find((customer) => customer.customer_id === id) || null;
}

function renderStatus() {
  if (!state.snapshot) {
    statusLine.textContent = "Connecting...";
    statusLine.className = "status-line";
    return;
  }

  if (state.notice && Date.now() < state.noticeUntil) {
    statusLine.textContent = state.notice.message;
    statusLine.className = state.notice.isError ? "status-line error" : "status-line ok";
    return;
  }

  state.notice = null;
  const snapshot = state.snapshot;
  const campaign = snapshot.campaign;
  const calendar = snapshot.calendar;
  const sim = snapshot.simulation;
  campaignLineNode.textContent = `${campaign.cafe_name} - Day ${calendar.day_index} - ${calendar.date_label}`;
  statusLine.className = "status-line";
  if (sim.phase === "closing") {
    statusLine.textContent = "Closing the doors and resolving the shift.";
  } else if (sim.running) {
    statusLine.textContent = `${calendar.sim_current_time} - service is live.`;
  } else if (calendar.phase === "settled") {
    statusLine.textContent = "Day settled. Read the recap and move to tomorrow.";
  } else {
    statusLine.textContent = "Planning. Prep the counter before opening.";
  }
}

function actionButton(label, className, disabled, run) {
  const button = createElement("button", className, label);
  button.type = "button";
  button.disabled = disabled;
  button.addEventListener("click", run);
  return button;
}

function renderPhaseActions(snapshot) {
  clear(phaseActionsNode);
  const sim = snapshot.simulation;
  const calendar = snapshot.calendar;

  if (getMode(snapshot) === "planning") {
    phaseActionsNode.appendChild(
      actionButton("Open day", "primary", sim.phase === "closing" || calendar.phase === "settled", () =>
        runControl(() => api("/api/day/start", "POST"), "Day opened.")
      )
    );
    return;
  }

  if (getMode(snapshot) === "history") {
    phaseActionsNode.appendChild(
      actionButton("Next day", "primary", sim.running || calendar.phase !== "settled", () => {
        state.events = [];
        state.eventCursor = 0;
        state.seenThoughts.clear();
        clearThoughtBubbles();
        return runControl(() => api("/api/day/advance", "POST"), "Advanced to next day.");
      })
    );
    return;
  }

  if (sim.running || sim.phase === "closing") {
    phaseActionsNode.appendChild(
      actionButton("Spawn customer", "secondary", sim.phase !== "running", () =>
        runControl(() => api("/api/control/spawn", "POST"), "Customer spawned.")
      )
    );
    phaseActionsNode.appendChild(
      actionButton("Close day", "primary", sim.phase !== "running" && sim.phase !== "closing", () =>
        runControl(() => api("/api/day/close", "POST"), "Day closed.")
      )
    );
    return;
  }

  phaseActionsNode.appendChild(
    actionButton("Settle day", "primary", sim.running || calendar.phase === "settled" || calendar.phase === "planning", () =>
      runControl(() => api("/api/day/settle", "POST"), "Day settled.")
    )
  );
}

function renderDayPulse(snapshot) {
  clear(dayPulseNode);
  const metrics = snapshot.metrics || {};
  const campaign = snapshot.campaign || {};
  const calendar = snapshot.calendar || {};
  const sim = snapshot.simulation || {};
  const openOrders = getOpenOrders(snapshot).length;
  const supplyAlerts = getSupplyAlerts(snapshot);
  const served = metrics.orders_delivered ?? 0;
  const alertLabel = supplyAlerts.length
    ? `${supplyAlerts.length} supply alert${supplyAlerts.length === 1 ? "" : "s"}`
    : "Supplies steady";

  [
    ["Day", `${calendar.day_index} / ${calendar.phase}`],
    ["Clock", calendar.sim_current_time || `${sim.elapsed_seconds || 0}s`],
    ["Revenue", formatMoney(metrics.revenue)],
    ["Served", String(served)],
    ["Open orders", String(openOrders)],
    ["Cash", formatMoney(campaign.money)],
    ["Rep", String(campaign.reputation)],
    ["Storage", alertLabel],
  ].forEach(([label, value]) => {
    const item = createElement("button", "pulse-item");
    item.type = "button";
    appendText(item, "span", null, label);
    appendText(item, "strong", null, value);
    item.addEventListener("click", () => selectItem(label === "Storage" ? "zone" : "pulse", label.toLowerCase()));
    dayPulseNode.appendChild(item);
  });
}

function renderPrep(snapshot) {
  clear(prepCounterNode);
  const sim = snapshot.simulation;
  const calendar = snapshot.calendar;
  const locked = sim.running || sim.phase === "closing" || calendar.phase === "settled";

  const supplyRail = createElement("div", "prep-supplies");
  appendText(supplyRail, "h3", null, "Restock");
  Object.entries(snapshot.supplies || {}).forEach(([supplyId, supply]) => {
    const row = selectable(createElement("div", `prep-line ${supply.status || "normal"}`), "supply", supplyId);
    appendText(row, "span", null, supply.name || supplyId);
    appendText(row, "strong", null, String(supply.quantity));
    const button = createElement("button", "tiny-action", "+5");
    button.type = "button";
    button.disabled = locked;
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      runControl(() => api("/api/restock", "POST", { supply_id: supplyId, quantity: 5 }), `Restocked ${supply.name || supplyId}.`);
    });
    row.appendChild(button);
    supplyRail.appendChild(row);
  });

  const menuRail = createElement("div", "prep-menu");
  appendText(menuRail, "h3", null, "Menu readiness");
  const menuEntries = Object.entries(snapshot.menu || {});
  const orderable = menuEntries.filter(([, item]) => item.orderable).length;
  const soldOut = menuEntries.filter(([, item]) => item.manually_available && !item.stock_available).length;
  const offMenu = menuEntries.filter(([, item]) => !item.manually_available).length;
  [
    ["Orderable", String(orderable)],
    ["Sold out", String(soldOut)],
    ["Off menu", String(offMenu)],
  ].forEach(([label, value]) => {
    const line = createElement("div", "prep-stat");
    appendText(line, "span", null, label);
    appendText(line, "strong", null, value);
    menuRail.appendChild(line);
  });

  const settingsRail = createElement("div", "prep-shift");
  appendText(settingsRail, "h3", null, "Shift shape");
  appendText(settingsRail, "p", null, `Customers every ${sim.spawn_interval}s for ${sim.sim_duration}s.`);
  appendText(settingsRail, "p", null, "Fine controls live in Back office.");

  prepCounterNode.append(supplyRail, menuRail, settingsRail);
}

function renderPerson(node, person, thinking, kind = "customer") {
  const avatar = createElement("div", `person ${kind}`);
  const name = createElement("div", "person-nameplate");
  appendText(name, "strong", null, person.display_name || person.name || person.customer_id || "Guest");
  appendText(name, "span", null, person.status || person.visit_phase || person.archetype_id || "active");
  avatar.appendChild(name);
  upsertThoughtBubble(avatar, kind === "customer" ? null : thinking?.summary, getThoughtKey(kind, person));
  node.appendChild(avatar);
  return avatar;
}

function renderStandingCustomers(snapshot) {
  clear(standingCustomersNode);
  const thinkingByAgent = getThinkingByAgent(snapshot);
  const standing = (snapshot.active_customers || []).filter((customer) => !customer.table_id);
  if (!standing.length) {
    appendText(standingCustomersNode, "div", "quiet-line", "No one at the door.");
    return;
  }
  standing.forEach((customer) => {
    const person = selectable(createElement("div", "person-slot"), "person", customer.customer_id);
    renderPerson(person, customer, thinkingByAgent.get(customer.customer_id), "customer");
    standingCustomersNode.appendChild(person);
  });
}

function renderStaff(snapshot) {
  clear(staffStageNode);
  appendText(staffStageNode, "div", "zone-title", "Baristas");
  const thinkingByAgent = getThinkingByAgent(snapshot);
  const staffEntries = Object.entries(snapshot.staff || {});
  if (!staffEntries.length) {
    appendText(staffStageNode, "div", "quiet-line", "No baristas on shift.");
    return;
  }
  staffEntries.forEach(([staffId, staff]) => {
    const row = selectable(createElement("div", `barista-line ${staff.status || "idle"}`), "person", staffId);
    const person = {
      id: staffId,
      display_name: staff.display_name || staffId,
      status: staff.current_order_id ? `${staff.status}: ${staff.current_order_id}` : staff.status || "idle",
    };
    renderPerson(row, person, thinkingByAgent.get(staffId), "barista");
    const count = createElement("div", "barista-count", `${staff.orders_completed ?? 0} done`);
    row.appendChild(count);
    staffStageNode.appendChild(row);
  });
}

function renderOrderTicket(order) {
  const ticket = selectable(createElement("button", `order-ticket ${order.status}`), "order", order.order_id);
  ticket.type = "button";
  appendText(ticket, "span", "ticket-status", pipelineLabels[order.status] || order.status);
  appendText(ticket, "strong", null, order.customer?.name || order.customer_id);
  appendText(ticket, "span", "ticket-items", formatItemList(order.item_names));
  if (order.barista_id) {
    appendText(ticket, "span", "ticket-owner", order.barista_id);
  }
  return ticket;
}

function renderQueue(snapshot) {
  clear(queueRiverNode);
  appendText(queueRiverNode, "div", "zone-title", "Order flow");
  activeOrderStatuses.forEach((status) => {
    const lane = createElement("div", `flow-lane ${status}`);
    const orders = (snapshot.queue || []).filter((order) => order.status === status);
    const label = createElement("div", "flow-label");
    appendText(label, "span", null, pipelineLabels[status]);
    appendText(label, "strong", null, String(orders.length));
    lane.appendChild(label);
    const tickets = createElement("div", "ticket-row");
    if (!orders.length) {
      appendText(tickets, "div", "quiet-line", "Clear");
    } else {
      orders.forEach((order) => tickets.appendChild(renderOrderTicket(order)));
    }
    lane.appendChild(tickets);
    queueRiverNode.appendChild(lane);
  });
}

function renderTables(snapshot) {
  clear(tablesMapNode);
  const thinkingByAgent = getThinkingByAgent(snapshot);
  (snapshot.tables || []).forEach((table) => {
    const node = selectable(createElement("div", `cafe-table ${table.status}`), "table", table.table_id);
    appendText(node, "span", "table-id", table.table_id.toUpperCase());
    const customer = table.customer;
    if (customer) {
      const customerSlot = createElement("div", "seated-person");
      renderPerson(customerSlot, customer, thinkingByAgent.get(customer.customer_id), "customer");
      node.appendChild(customerSlot);
      const order = getCustomerOrder(snapshot, customer.customer_id);
      appendText(node, "span", "table-note", order ? `${pipelineLabels[order.status]} order` : customer.visit_phase || "seated");
    } else {
      appendText(node, "span", "table-note", "Open");
    }
    tablesMapNode.appendChild(node);
  });
}

function renderStorage(snapshot) {
  clear(storageZoneNode);
  appendText(storageZoneNode, "div", "zone-title", "Storage");
  const alerts = getSupplyAlerts(snapshot);
  const entries = alerts.length ? alerts : Object.entries(snapshot.supplies || {}).slice(0, 3);
  entries.forEach(([supplyId, supply]) => {
    const line = selectable(createElement("div", `supply-line ${supply.status || "normal"}`), "supply", supplyId);
    appendText(line, "span", null, supply.name || supplyId);
    appendText(line, "strong", null, `${supply.quantity}`);
    storageZoneNode.appendChild(line);
  });
  if (!entries.length) {
    appendText(storageZoneNode, "div", "quiet-line", "No supplies tracked.");
  }
}

function renderRegister(snapshot) {
  clear(registerZoneNode);
  const metrics = snapshot.metrics || {};
  appendText(registerZoneNode, "div", "zone-title", "Register");
  appendText(registerZoneNode, "strong", "register-money", formatMoney(metrics.revenue));
  appendText(registerZoneNode, "span", null, `${metrics.orders_delivered ?? 0} picked up`);
}

function renderHistory(snapshot) {
  clear(historyWallNode);
  const history = snapshot.history || {};
  const summary = snapshot.day_summary;
  const timeline = history.timeline || [];

  const timelineNode = createElement("div", "history-timeline");
  if (!timeline.length) {
    appendText(timelineNode, "div", "quiet-line", "No completed days yet.");
  }
  timeline.forEach((day) => {
    const dayNode = selectable(createElement("button", `day-mark${day.active ? " active" : ""}`), "history-day", day.day_id || day.day_index);
    dayNode.type = "button";
    appendText(dayNode, "span", null, `Day ${day.day_index}`);
    appendText(dayNode, "strong", null, day.phase === "settled" ? formatMoney(day.profit) : day.phase);
    appendText(dayNode, "small", null, `${day.customers_served ?? 0} served`);
    timelineNode.appendChild(dayNode);
  });

  const recap = createElement("div", "history-recap");
  appendText(recap, "h3", null, "Latest day");
  if (!summary) {
    appendText(recap, "p", null, "Settle a day to see profit, satisfaction, reputation, and tomorrow's warnings.");
  } else {
    [
      ["Revenue", formatMoney(summary.revenue)],
      ["Profit", formatMoney(summary.profit)],
      ["Served", String(summary.customers_served)],
      ["Lost", String(summary.customers_lost)],
      ["Satisfaction", `${summary.satisfaction}/100`],
      ["Reputation", `${summary.reputation_delta >= 0 ? "+" : ""}${summary.reputation_delta}`],
    ].forEach(([label, value]) => {
      const row = createElement("div", "recap-line");
      appendText(row, "span", null, label);
      appendText(row, "strong", null, value);
      recap.appendChild(row);
    });
    (summary.tomorrow_warnings || []).slice(0, 4).forEach((warning) => appendText(recap, "p", "warning-text", warning));
  }

  historyWallNode.append(timelineNode, recap);
}

function renderMenu(snapshot) {
  clear(menuNode);
  Object.entries(snapshot.menu || {}).forEach(([itemId, item]) => {
    const availabilityClass = item.orderable ? "orderable" : item.manually_available ? "sold-out" : "off-menu";
    const row = selectable(createElement("div", `menu-line ${availabilityClass}`), "menu", itemId);
    const label = createElement("div");
    appendText(label, "strong", null, item.name);
    appendText(label, "span", null, `${formatMoney(item.price)} - ${item.category || "item"}`);

    const toggleLabel = createElement("label", "menu-toggle");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = item.manually_available ?? item.available;
    input.addEventListener("click", (event) => event.stopPropagation());
    input.addEventListener("change", async () => {
      try {
        await api(`/api/control/menu/${itemId}`, "POST", { available: input.checked });
        setNotice(`${input.checked ? "Enabled" : "Disabled"} ${item.name}.`);
        await refreshSnapshot();
      } catch (error) {
        input.checked = !input.checked;
        setNotice("Could not update menu item.", true);
      }
    });
    toggleLabel.appendChild(input);
    toggleLabel.appendChild(document.createTextNode(input.checked ? "On" : "Off"));
    row.append(label, toggleLabel);
    menuNode.appendChild(row);
  });
}

function renderEvents() {
  clear(eventLogNode);
  if (!state.events.length) {
    appendText(eventLogNode, "div", "quiet-line", "No actions logged yet.");
    return;
  }
  [...state.events].reverse().slice(0, 20).forEach((event, index) => {
    const row = selectable(createElement("button", "event-line"), "event", `${event.t}:${index}`);
    row.type = "button";
    row.dataset.eventIndex = String(state.events.length - index - 1);
    appendText(row, "span", null, event.sim_time || formatTime(event.t));
    appendText(row, "strong", null, `${event.agent}: ${event.action}`);
    eventLogNode.appendChild(row);
  });
}

function renderInspectorEmpty() {
  clear(inspectorContentNode);
  appendText(inspectorContentNode, "span", "eyebrow", "Inspector");
  appendText(inspectorContentNode, "h2", null, "Select something on the floor.");
  appendText(inspectorContentNode, "p", null, "People, tables, orders, supplies, history, and activity all open here.");
}

function addInspectorRows(rows) {
  const list = createElement("div", "inspector-rows");
  rows.forEach(([label, value]) => {
    const row = createElement("div", "inspector-row");
    appendText(row, "span", null, label);
    appendText(row, "strong", null, value == null || value === "" ? "none" : String(value));
    list.appendChild(row);
  });
  inspectorContentNode.appendChild(list);
}

function renderInspector() {
  if (!state.snapshot || !state.selected) {
    renderInspectorEmpty();
    return;
  }

  const snapshot = state.snapshot;
  const { kind, id } = state.selected;
  clear(inspectorContentNode);
  appendText(inspectorContentNode, "span", "eyebrow", kind);

  if (kind === "person") {
    const person = getPerson(snapshot, id);
    const thinking = getThinkingByAgent(snapshot).get(id);
    if (!person) {
      appendText(inspectorContentNode, "h2", null, "Person not active");
      return;
    }
    appendText(inspectorContentNode, "h2", null, person.display_name || person.name || id);
    if (person.kind === "staff") {
      addInspectorRows([
        ["Status", person.status || "active"],
        ["Current order", person.current_order_id],
        ["Completed", person.orders_completed],
        ["Last action", person.last_action],
      ]);
    } else {
      addInspectorRows([
        ["Status", person.visit_phase || "active"],
        ["Archetype", person.archetype_id],
        ["Budget", formatMoney(person.budget)],
        ["Spent", formatMoney(person.budget_spent)],
        ["Patience", person.patience],
        ["Seat need", person.seat_need],
        ["Orders", person.orders_placed],
        ["Current order", person.active_order_id || person.order_id],
        ["Order status", person.order_status],
        ["Dwell target", person.dwell_seconds_target ? `${person.dwell_seconds_target}s` : null],
        ["Leave reason", person.leave_reason],
        ["Held", formatItemList(person.held_item_names)],
        ["Consumed", formatItemList(person.consumed_item_names)],
      ]);
    }
    if (person.kind === "staff" && thinking?.summary) {
      appendText(inspectorContentNode, "h3", null, "Reasoning summary");
      appendText(inspectorContentNode, "p", "thought-full", thinking.summary);
    }
    return;
  }

  if (kind === "order") {
    const order = (snapshot.queue || []).find((entry) => entry.order_id === id);
    if (!order) {
      appendText(inspectorContentNode, "h2", null, "Order not found");
      return;
    }
    appendText(inspectorContentNode, "h2", null, order.order_id);
    addInspectorRows([
      ["Customer", order.customer?.name || order.customer_id],
      ["Items", formatItemList(order.item_names)],
      ["Status", pipelineLabels[order.status] || order.status],
      ["Barista", order.barista_id || order.completed_by],
      ["Total", formatMoney(order.total_price)],
      ["Close reason", order.close_reason],
    ]);
    return;
  }

  if (kind === "table") {
    const table = (snapshot.tables || []).find((entry) => entry.table_id === id);
    appendText(inspectorContentNode, "h2", null, id.toUpperCase());
    if (!table) {
      appendText(inspectorContentNode, "p", null, "Table not found.");
      return;
    }
    addInspectorRows([
      ["Status", table.status],
      ["Customer", table.customer?.name],
      ["Archetype", table.customer?.archetype_id],
      ["Visit phase", table.customer?.visit_phase],
      ["Order", table.customer ? getCustomerOrder(snapshot, table.customer.customer_id)?.order_id : null],
    ]);
    return;
  }

  if (kind === "supply") {
    const supply = (snapshot.supplies || {})[id];
    appendText(inspectorContentNode, "h2", null, supply?.name || id);
    addInspectorRows([
      ["Quantity", supply?.quantity],
      ["Status", supply?.status],
      ["Low threshold", supply?.low_threshold],
    ]);
    return;
  }

  if (kind === "menu") {
    const item = (snapshot.menu || {})[id];
    appendText(inspectorContentNode, "h2", null, item?.name || id);
    const missing = Object.values(item?.missing_supplies || {}).map((supply) => supply.name).join(", ");
    addInspectorRows([
      ["Price", item ? formatMoney(item.price) : null],
      ["Prep", item?.prep_seconds ? `${item.prep_seconds}s` : null],
      ["Category", item?.category],
      ["Orderable", item?.orderable ? "yes" : "no"],
      ["Missing", missing],
    ]);
    return;
  }

  if (kind === "history-day") {
    appendText(inspectorContentNode, "h2", null, `Day ${id}`);
    appendText(inspectorContentNode, "p", null, "The full history mode keeps settled day performance visible after service.");
    return;
  }

  if (kind === "event") {
    const event = state.events[Number(document.querySelector(`[data-select-key="event:${id}"]`)?.dataset.eventIndex)];
    appendText(inspectorContentNode, "h2", null, event ? `${event.agent}: ${event.action}` : "Event");
    if (event) {
      addInspectorRows([
        ["Time", event.sim_time || formatTime(event.t)],
        ["Agent", event.agent],
        ["Action", event.action],
      ]);
      appendText(inspectorContentNode, "p", null, event.detail || "No detail.");
    }
    return;
  }

  if (kind === "zone" && id === "storage") {
    appendText(inspectorContentNode, "h2", null, "Storage");
    Object.entries(snapshot.supplies || {}).forEach(([supplyId, supply]) => {
      addInspectorRows([[supply.name || supplyId, `${supply.quantity} - ${supply.status}`]]);
    });
    return;
  }

  appendText(inspectorContentNode, "h2", null, "Day pulse");
  addInspectorRows([
    ["Revenue", formatMoney(snapshot.metrics?.revenue)],
    ["Cash", formatMoney(snapshot.campaign?.money)],
    ["Reputation", snapshot.campaign?.reputation],
    ["Open orders", getOpenOrders(snapshot).length],
    ["Supply alerts", getSupplyAlerts(snapshot).length],
  ]);
}

function renderSnapshot(snapshot) {
  state.snapshot = snapshot;
  const sim = snapshot.simulation;
  appNode.dataset.mode = getMode(snapshot);
  spawnIntervalInput.value = sim.spawn_interval;
  simDurationInput.value = sim.sim_duration;
  stopBtn.disabled = sim.phase !== "running" && sim.phase !== "closing";
  resetBtn.disabled = sim.phase === "closing";
  saveSettingsBtn.disabled = sim.phase === "closing";

  beginThoughtRender();
  renderStatus();
  renderPhaseActions(snapshot);
  renderDayPulse(snapshot);
  renderPrep(snapshot);
  renderStandingCustomers(snapshot);
  renderStaff(snapshot);
  renderQueue(snapshot);
  renderTables(snapshot);
  renderStorage(snapshot);
  renderRegister(snapshot);
  renderHistory(snapshot);
  renderMenu(snapshot);
  pruneThoughtBubbles();
  renderInspector();
  markSelected();
}

async function loadRecentEvents() {
  const data = await api(`/api/events?after=${state.eventCursor}&limit=50`);
  state.eventCursor = data.next_cursor;
  if (!data.events.length) {
    return;
  }
  state.events = [...state.events, ...data.events].slice(-80);
  renderEvents();
}

async function runControl(action, successMessage) {
  try {
    await action();
    await refreshSnapshot();
    if (successMessage) {
      setNotice(successMessage);
    }
  } catch (error) {
    setNotice(error.message || "Control request failed.", true);
  }
}

async function refreshSnapshot() {
  const snapshot = await api("/api/snapshot");
  renderSnapshot(snapshot);
}

function attachControls() {
  inspectorCloseBtn.addEventListener("click", () => {
    state.selected = null;
    renderInspector();
    markSelected();
  });
  stopBtn.addEventListener("click", () =>
    runControl(() => api("/api/control/stop", "POST"), "Simulation stopped.")
  );
  resetBtn.addEventListener("click", () => {
    state.events = [];
    state.eventCursor = 0;
    state.seenThoughts.clear();
    clearThoughtBubbles();
    renderEvents();
    return runControl(() => api("/api/control/reset", "POST"), "Simulation reset.");
  });
  saveSettingsBtn.addEventListener("click", () =>
    runControl(
      () =>
        api("/api/control/settings", "POST", {
          spawn_interval: Number(spawnIntervalInput.value),
          sim_duration: Number(simDurationInput.value),
        }),
      "Settings applied."
    )
  );
}

async function bootstrap() {
  attachControls();
  renderEvents();
  renderInspectorEmpty();
  await refreshSnapshot();
  await loadRecentEvents();

  const stream = new EventSource("/api/stream");
  stream.addEventListener("snapshot", (event) => {
    const snapshot = JSON.parse(event.data);
    renderSnapshot(snapshot);
  });
  stream.onerror = () => {
    statusLine.textContent = "Disconnected. Retrying stream...";
  };

  setInterval(() => {
    loadRecentEvents().catch(() => {});
  }, 1200);
}

bootstrap().catch(() => {
  statusLine.textContent = "Failed to load dashboard API.";
});
