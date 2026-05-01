const state = {
  snapshot: null,
  eventCursor: 0,
  events: [],
  notice: null,
  noticeUntil: 0,
  expanded: new Set(),
  seenThoughts: new Set(),
};

const statusLine = document.getElementById("status-line");
const kpisNode = document.getElementById("kpis");
const tablesNode = document.getElementById("tables");
const activeCustomersNode = document.getElementById("active-customers");
const pipelineNode = document.getElementById("pipeline");
const menuNode = document.getElementById("menu-list");
const eventLogNode = document.getElementById("event-log");
const counterLabelNode = document.getElementById("counter-label");
const staffListNode = document.getElementById("staff-list");
const suppliesListNode = document.getElementById("supplies-list");

const startBtn = document.getElementById("start-btn");
const stopBtn = document.getElementById("stop-btn");
const resetBtn = document.getElementById("reset-btn");
const spawnBtn = document.getElementById("spawn-btn");
const saveSettingsBtn = document.getElementById("save-settings-btn");
const spawnIntervalInput = document.getElementById("spawn-interval");
const simDurationInput = document.getElementById("sim-duration");

const pipelineOrder = ["pending", "claimed", "preparing", "ready", "delivered", "abandoned", "stale", "failed"];
const openOrderStatuses = new Set(["pending", "claimed", "preparing", "ready"]);
const pipelineLabels = {
  pending: "Waiting",
  claimed: "Claimed",
  preparing: "In prep",
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

function formatTime(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatMoney(value) {
  return `$${Number(value || 0).toFixed(2)}`;
}

function formatItemList(items) {
  return (items || []).length ? items.join(", ") : "none";
}

function clampText(text, maxLength = 92) {
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

  return clampText([...new Set(matches)].slice(0, 2).join(" / "));
}

function getThinkingByAgent(snapshot) {
  return new Map((snapshot.agent_thinking || []).map((entry) => [entry.agent_id, entry]));
}

function appendThoughtBubble(parent, summary, className = "", key = "") {
  const thought = extractBoldThought(summary);
  if (!thought) {
    return null;
  }

  const fingerprint = `${key}:${thought}`;
  const isSettled = state.seenThoughts.has(fingerprint);
  state.seenThoughts.add(fingerprint);
  const bubble = createElement(
    "div",
    `thought-bubble${className ? ` ${className}` : ""}${isSettled ? " is-settled" : ""}`,
    thought
  );
  bubble.title = thought;
  parent.appendChild(bubble);
  return bubble;
}

function toggleExpanded(key) {
  if (state.expanded.has(key)) {
    state.expanded.delete(key);
  } else {
    state.expanded.add(key);
  }
}

function makeExpandable(node, key, render) {
  node.classList.toggle("is-expanded", state.expanded.has(key));
  node.addEventListener("click", (event) => {
    if (event.target.matches("input, button, label")) {
      return;
    }
    toggleExpanded(key);
    render();
  });
}

function setNotice(message, isError = false) {
  state.notice = { message, isError };
  state.noticeUntil = Date.now() + 3500;
  renderStatus();
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
  statusLine.className = "status-line";
  const sim = state.snapshot.simulation;
  if (sim.phase === "closing") {
    statusLine.textContent = `Closing - ${sim.elapsed_seconds}s elapsed - ${sim.spawn_count} spawned`;
  } else if (sim.running) {
    statusLine.textContent = `Running - ${sim.elapsed_seconds}s elapsed - ${sim.spawn_count} spawned`;
  } else {
    statusLine.textContent = "Stopped - ready for operator input";
  }
}

function renderKpis(snapshot) {
  const metrics = snapshot.metrics;
  const sim = snapshot.simulation;
  const occupiedTables = snapshot.tables.filter((table) => table.status === "occupied").length;
  const openOrders = snapshot.queue.filter((order) => openOrderStatuses.has(order.status)).length;
  const staffEntries = Object.entries(snapshot.staff || {});
  const completions = staffEntries
    .map(([staffId, staff]) => `${staff.display_name} ${metrics.orders_completed_by_barista?.[staffId] ?? 0}`)
    .join(" / ");
  const idleChecks = staffEntries
    .map(([staffId, staff]) => `${staff.display_name} ${metrics.idle_checks_by_barista?.[staffId] ?? 0}`)
    .join(" / ");
  const conflictsByStaff = staffEntries
    .map(([staffId, staff]) => `${staff.display_name} ${metrics.claim_conflicts_by_barista?.[staffId] ?? 0}`)
    .join(" / ");
  const chips = [
    ["Revenue", formatMoney(metrics.revenue)],
    ["Open orders", String(openOrders)],
    ["Tables", `${occupiedTables}/${snapshot.tables.length}`],
    ["Customers", String(snapshot.active_customers.length)],
    ["Elapsed", `${sim.elapsed_seconds}s`],
    ["Avg ready", metrics.average_wait_seconds == null ? "n/a" : `${metrics.average_wait_seconds}s`],
    ["Avg prep", metrics.average_prep_seconds == null ? "n/a" : `${metrics.average_prep_seconds}s`],
    ["Conflicts", String(metrics.claim_conflicts ?? 0)],
    ["Conflict split", conflictsByStaff || "n/a"],
    ["Completed", completions || "n/a"],
    ["Idle checks", idleChecks || "n/a"],
  ];

  clear(kpisNode);
  chips.forEach(([label, value]) => {
    const chip = createElement("div", "chip");
    appendText(chip, "div", "label", label);
    appendText(chip, "div", "value", value);
    kpisNode.appendChild(chip);
  });
}

function getCustomerOrder(snapshot, customerId) {
  return snapshot.queue
    .filter((order) => order.customer_id === customerId)
    .find((order) => openOrderStatuses.has(order.status));
}

function renderTables(snapshot) {
  clear(tablesNode);
  const thinkingByAgent = getThinkingByAgent(snapshot);
  snapshot.tables.forEach((table) => {
    const tableNode = createElement("div", `table ${table.status}`);
    const customer = table.customer;
    const order = customer ? getCustomerOrder(snapshot, customer.customer_id) : null;
    const head = createElement("div", "table-head");
    appendText(head, "div", "table-id", table.table_id.toUpperCase());
    appendText(head, "div", "table-state", table.status === "occupied" ? "Occupied" : "Empty");
    tableNode.appendChild(head);

    const body = createElement("div", "table-body");
    if (customer) {
      const nameRow = createElement("div", "person-thinking-row");
      appendText(nameRow, "div", "person-name", customer.name);
      appendThoughtBubble(
        nameRow,
        thinkingByAgent.get(customer.customer_id)?.summary,
        "table-thought",
        `table:${customer.customer_id}`
      );
      body.appendChild(nameRow);
      appendText(
        body,
        "div",
        "small muted",
        `${customer.mood} - ${customer.visit_phase || "arrived"} - ${customer.waiting_seconds}s`
      );
      if (order) {
        appendText(body, "div", "small", `${pipelineLabels[order.status]}: ${order.item_names.join(", ")}`);
      } else if ((customer.held_item_names || []).length) {
        appendText(body, "div", "small", `Holding: ${formatItemList(customer.held_item_names)}`);
      } else {
        appendText(body, "div", "small muted", "No open order");
      }
      if ((customer.consumed_item_names || []).length) {
        appendText(body, "div", "small muted", `Consumed: ${formatItemList(customer.consumed_item_names)}`);
      }
      appendText(body, "div", "details", `${customer.customer_id}${order ? ` - ${order.order_id} - ${formatMoney(order.total_price)}` : ""}`);
    } else {
      appendText(body, "div", "muted", "Open table");
      appendText(body, "div", "details", "No customer has claimed this table.");
    }
    tableNode.appendChild(body);
    makeExpandable(tableNode, `table:${table.table_id}`, () => renderTables(snapshot));
    tablesNode.appendChild(tableNode);
  });
}

function renderCounterThinking(snapshot) {
  if (!counterLabelNode) {
    return;
  }

  counterLabelNode.replaceChildren();
  appendText(counterLabelNode, "span", "counter-title", "Counter");
}

function createStaffRow(staffId) {
  const row = createElement("div", "staff-row");
  row.dataset.staffId = staffId;

  const head = createElement("div", "row-head");
  appendText(head, "strong", "staff-name", staffId);
  appendText(head, "span", "staff-status", "idle");
  row.appendChild(head);

  appendText(row, "div", "small staff-current", "Current order: none");
  appendText(row, "div", "small muted staff-completed", "Completed: 0");
  appendText(row, "div", "small muted staff-last", "Last action: none");
  appendText(row, "div", "staff-thinking muted", "Thinking: No thinking summary yet.");
  makeExpandable(row, `staff:${staffId}`, renderSnapshotFromState);
  return row;
}

function updateStaffThinking(row, thinking) {
  const summary = thinking?.summary || "";
  const nextKey = `${thinking?.updated_at || ""}:${summary}`;
  if (row.dataset.thinkingKey === nextKey) {
    return;
  }

  const thinkingNode = row.querySelector(".staff-thinking");
  thinkingNode.textContent = summary
    ? `Thinking: ${clampText(summary, 140)}`
    : "Thinking: No thinking summary yet.";
  thinkingNode.classList.toggle("muted", !summary);
  row.dataset.thinkingKey = nextKey;
}

function renderStaff(snapshot) {
  const staffEntries = Object.entries(snapshot.staff || {});
  if (!staffEntries.length) {
    clear(staffListNode);
    appendText(staffListNode, "div", "empty-state", "No staff on shift.");
    return;
  }

  const visibleStaffIds = new Set();
  const thinkingByAgent = getThinkingByAgent(snapshot);
  staffEntries.forEach(([staffId, staff]) => {
    visibleStaffIds.add(staffId);
    let row = staffListNode.querySelector(`[data-staff-id="${staffId}"]`);
    if (!row) {
      row = createStaffRow(staffId);
      staffListNode.appendChild(row);
    }

    row.className = `staff-row ${staff.status || "idle"}`;
    row.querySelector(".staff-name").textContent = staff.display_name || staffId;
    row.querySelector(".staff-status").textContent = staff.status || "idle";
    row.querySelector(".staff-current").textContent = `Current order: ${staff.current_order_id || "none"}`;
    row.querySelector(".staff-completed").textContent = `Completed: ${staff.orders_completed ?? 0}`;
    row.querySelector(".staff-last").textContent = `Last action: ${staff.last_action || "none"}`;
    updateStaffThinking(row, thinkingByAgent.get(staffId));
  });

  staffListNode.querySelectorAll("[data-staff-id]").forEach((row) => {
    if (!visibleStaffIds.has(row.dataset.staffId)) {
      row.remove();
    }
  });
}

function renderSupplies(snapshot) {
  clear(suppliesListNode);
  const entries = Object.entries(snapshot.supplies || {});
  if (!entries.length) {
    appendText(suppliesListNode, "div", "empty-state", "No supplies tracked.");
    return;
  }

  entries.forEach(([supplyId, supply]) => {
    const row = createElement("div", `supply-row ${supply.status || "normal"}`);
    const head = createElement("div", "row-head");
    appendText(head, "strong", null, supply.name || supplyId);
    appendText(head, "span", "supply-status", supply.status || "normal");
    row.appendChild(head);
    appendText(row, "div", "small muted", `${supply.quantity} left`);
    suppliesListNode.appendChild(row);
  });
}

function renderActiveCustomers(snapshot) {
  clear(activeCustomersNode);
  if (!snapshot.active_customers.length) {
    appendText(activeCustomersNode, "div", "muted small", "No active customers");
    return;
  }

  const thinkingByAgent = getThinkingByAgent(snapshot);
  snapshot.active_customers.forEach((customer) => {
    const isStanding = !customer.table_id;
    const pill = createElement("div", isStanding ? "presence-pill has-thought-slot" : "presence-pill");
    appendText(
      pill,
      "span",
      "presence-person",
      `${customer.name} - ${customer.table_id || "standing"} - ${customer.visit_phase || "arrived"}`
    );
    if (isStanding) {
      appendThoughtBubble(
        pill,
        thinkingByAgent.get(customer.customer_id)?.summary,
        "presence-thought",
        `presence:${customer.customer_id}`
      );
    }
    activeCustomersNode.appendChild(pill);
  });
}

function renderOrderRow(order) {
  const item = createElement("div", "list-item order-row");
  const head = createElement("div", "row-head");
  appendText(head, "strong", null, order.customer?.name || order.customer_id);
  appendText(head, "span", "order-status", pipelineLabels[order.status] || order.status);
  item.appendChild(head);
  appendText(item, "div", "small", order.item_names.join(", "));
  appendText(
    item,
    "div",
    "details",
    `${order.order_id} - ${formatMoney(order.total_price)}${order.barista_id ? ` - ${order.barista_id}` : ""}`
  );
  makeExpandable(item, `order:${order.order_id}`, renderSnapshotFromState);
  return item;
}

function renderPipeline(snapshot) {
  clear(pipelineNode);
  pipelineOrder.forEach((lane) => {
    const items = snapshot.queue.filter((order) => order.status === lane);
    const laneNode = createElement("div", "lane");
    const title = createElement("div", "title");
    appendText(title, "span", null, pipelineLabels[lane]);
    appendText(title, "span", null, String(items.length));
    laneNode.appendChild(title);

    const itemList = createElement("div", "lane-items");
    if (!items.length) {
      appendText(itemList, "div", "empty-state", "Clear");
    } else {
      items.forEach((order) => itemList.appendChild(renderOrderRow(order)));
    }
    laneNode.appendChild(itemList);
    pipelineNode.appendChild(laneNode);
  });
}

function renderMenu(snapshot) {
  clear(menuNode);
  Object.entries(snapshot.menu).forEach(([itemId, item]) => {
    const row = createElement("div", "menu-item");
    const label = createElement("div");
    appendText(label, "strong", null, item.name);
    appendText(label, "div", "small muted", `${formatMoney(item.price)} - ${item.prep_seconds}s prep - ${item.category}`);
    appendText(label, "div", "details", `${itemId} is ${item.available ? "available" : "off menu"} for incoming customers.`);

    const toggleLabel = createElement("label", "menu-toggle");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = item.available;
    input.dataset.itemId = itemId;
    toggleLabel.appendChild(input);
    toggleLabel.appendChild(document.createTextNode(item.available ? "On" : "Off"));

    row.appendChild(label);
    row.appendChild(toggleLabel);
    makeExpandable(row, `menu:${itemId}`, () => renderMenu(snapshot));
    menuNode.appendChild(row);
  });

  menuNode.querySelectorAll("input[type=checkbox]").forEach((checkbox) => {
    checkbox.addEventListener("change", async (event) => {
      const input = event.target;
      const itemId = input.dataset.itemId;
      try {
        await api(`/api/control/menu/${itemId}`, "POST", { available: input.checked });
        setNotice(`${input.checked ? "Enabled" : "Disabled"} ${itemId}.`);
      } catch (error) {
        input.checked = !input.checked;
        setNotice("Could not update menu item.", true);
      }
    });
  });
}

function renderEvents() {
  clear(eventLogNode);
  if (!state.events.length) {
    appendText(eventLogNode, "div", "empty-state", "No actions logged yet.");
    return;
  }

  [...state.events].reverse().forEach((event, index) => {
    const key = `event:${event.t}:${index}`;
    const row = createElement("div", "event-item");
    const head = createElement("div", "row-head");
    const left = createElement("div");
    appendText(left, "div", "event-agent", event.agent);
    appendText(left, "div", "event-action", event.action);
    appendText(head, "span", "event-time", formatTime(event.t));
    head.appendChild(left);
    row.appendChild(head);
    appendText(row, "div", "details", event.detail);
    makeExpandable(row, key, renderEvents);
    eventLogNode.appendChild(row);
  });
}

function renderSnapshot(snapshot) {
  state.snapshot = snapshot;
  const sim = snapshot.simulation;

  spawnIntervalInput.value = sim.spawn_interval;
  simDurationInput.value = sim.sim_duration;
  startBtn.disabled = sim.phase === "running" || sim.phase === "closing";
  stopBtn.disabled = sim.phase !== "running" && sim.phase !== "closing";
  spawnBtn.disabled = sim.phase !== "running";
  resetBtn.disabled = sim.phase === "closing";
  saveSettingsBtn.disabled = sim.phase === "closing";
  renderSnapshotFromState();
}

function renderSnapshotFromState() {
  if (!state.snapshot) {
    return;
  }
  renderStatus();
  renderKpis(state.snapshot);
  renderCounterThinking(state.snapshot);
  renderStaff(state.snapshot);
  renderSupplies(state.snapshot);
  renderTables(state.snapshot);
  renderActiveCustomers(state.snapshot);
  renderPipeline(state.snapshot);
  renderMenu(state.snapshot);
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
    if (successMessage) {
      setNotice(successMessage);
    }
  } catch (error) {
    setNotice(error.message || "Control request failed.", true);
  }
}

function attachControls() {
  startBtn.addEventListener("click", () =>
    runControl(() => api("/api/control/start", "POST"), "Simulation started.")
  );
  stopBtn.addEventListener("click", () =>
    runControl(() => api("/api/control/stop", "POST"), "Simulation stopped.")
  );
  resetBtn.addEventListener("click", () => {
    clear(eventLogNode);
    state.events = [];
    state.eventCursor = 0;
    state.seenThoughts.clear();
    return runControl(() => api("/api/control/reset", "POST"), "Simulation reset.");
  });
  spawnBtn.addEventListener("click", () =>
    runControl(() => api("/api/control/spawn", "POST"), "Customer spawned.")
  );
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
  const firstSnapshot = await api("/api/snapshot");
  renderSnapshot(firstSnapshot);
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
