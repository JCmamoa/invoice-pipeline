const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const STATUS_LABELS = {
  queued: "Queued",
  processing: "Processing",
  processed: "Processed",
  needs_review: "Needs review",
  failed: "Failed",
};

const state = { invoices: [], filter: "", selectedId: null, timer: null };

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const isJson = (response.headers.get("content-type") || "").includes("application/json");
  const body = isJson ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = isJson ? body.detail || JSON.stringify(body) : body;
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return body;
}

function money(value, currency = "BRL") {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  if (Number.isNaN(number)) return escapeHtml(value);
  return `${currency} ${number.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function formatDate(value) {
  if (!value) return "—";
  const text = String(value).slice(0, 10);
  const date = new Date(`${text}T00:00:00`);
  if (Number.isNaN(date.getTime())) return escapeHtml(value);
  return date.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
}

function formatDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return escapeHtml(value);
  return date.toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatCnpj(value) {
  const digits = String(value || "").replace(/\D/g, "");
  if (digits.length !== 14) return value || "—";
  return `${digits.slice(0, 2)}.${digits.slice(2, 5)}.${digits.slice(5, 8)}/${digits.slice(8, 12)}-${digits.slice(12)}`;
}

function statusBadge(status) {
  const label = STATUS_LABELS[status] || status;
  return `<span class="status status-${escapeHtml(status)}">${escapeHtml(label)}</span>`;
}

function setConnection(ok) {
  const node = $("#conn");
  node.textContent = ok ? "live" : "offline";
  node.className = `conn ${ok ? "ok" : "bad"}`;
}

function toast(message, kind = "") {
  const node = $("#toast");
  node.textContent = message;
  node.className = `toast ${kind}`;
  clearTimeout(node._timer);
  node._timer = setTimeout(() => node.classList.add("hidden"), 4200);
}

function renderStats(stats) {
  const cards = [
    ["Total", stats.total ?? 0, ""],
    ["Processed", stats.processed ?? 0, "green"],
    ["Needs review", stats.needs_review ?? 0, "amber"],
    ["Failed", stats.failed ?? 0, "red"],
    ["In queue", stats.queue_depth ?? 0, "blue"],
    ["Processing", stats.processing ?? 0, "blue"],
  ];
  $("#stats").innerHTML = cards
    .map(
      ([label, value, tone]) => `
      <div class="stat ${tone}">
        <div class="stat-value">${escapeHtml(value)}</div>
        <div class="stat-label">${escapeHtml(label)}</div>
      </div>`,
    )
    .join("");
}

function renderInvoices() {
  const rows = $("#invoice-rows");
  const empty = $("#empty-state");
  if (!state.invoices.length) {
    rows.innerHTML = "";
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");
  rows.innerHTML = state.invoices
    .map((invoice) => {
      const supplier = invoice.supplier_name || "—";
      const total = money(invoice.total_amount, invoice.currency || "BRL");
      return `
        <tr data-id="${escapeHtml(invoice.id)}">
          <td><strong>${escapeHtml(supplier)}</strong><br /><span class="small muted">${escapeHtml(invoice.original_filename || "")}</span></td>
          <td>${escapeHtml(formatCnpj(invoice.supplier_cnpj))}</td>
          <td>${total}</td>
          <td>${escapeHtml(formatDate(invoice.due_date))}</td>
          <td>${statusBadge(invoice.status)}</td>
          <td>${escapeHtml(formatDateTime(invoice.created_at))}</td>
        </tr>`;
    })
    .join("");
}

function renderNotifications(items) {
  const feed = $("#notifications");
  if (!items.length) {
    feed.innerHTML = '<p class="muted small">No notifications yet.</p>';
    return;
  }
  feed.innerHTML = items
    .map((item) => {
      const tone = item.status === "sent" ? "success" : item.status === "failed" ? "failed" : "";
      return `
        <div class="feed-item ${tone}">
          <p>${escapeHtml(item.message || "—")}</p>
          <div class="feed-meta">
            <span>${escapeHtml(item.channel)} · ${escapeHtml(item.status)}</span>
            <span>${escapeHtml(formatDateTime(item.created_at))}</span>
          </div>
        </div>`;
    })
    .join("");
}

async function refresh() {
  try {
    const query = state.filter ? `?status=${encodeURIComponent(state.filter)}` : "";
    const [stats, list, notifications] = await Promise.all([
      api("/api/stats"),
      api(`/api/invoices${query}`),
      api("/api/notifications?limit=12"),
    ]);
    setConnection(true);
    renderStats(stats);
    state.invoices = list.invoices || [];
    renderInvoices();
    renderNotifications(notifications.notifications || []);
  } catch (error) {
    setConnection(false);
    console.error(error);
  }
}

function chips(errors, warnings) {
  const items = [
    ...(errors || []).map((item) => `<span class="chip chip-error">${escapeHtml(item)}</span>`),
    ...(warnings || []).map((item) => `<span class="chip chip-warn">${escapeHtml(item)}</span>`),
  ];
  if (!items.length) items.push('<span class="chip chip-ok">All required fields validated</span>');
  return items.join("");
}

function renderDetail(data) {
  const invoice = data.invoice;
  const notifications = data.notifications || [];
  const audit = data.audit || [];
  const extraction = invoice.extraction || {};

  $("#detail-content").innerHTML = `
    <div class="detail-head">
      <div>
        <h2>${escapeHtml(invoice.supplier_name || "Invoice under review")}</h2>
        <p class="detail-sub">${escapeHtml(invoice.original_filename)} · ${escapeHtml(invoice.source)} · ${escapeHtml(formatDateTime(invoice.created_at))}</p>
      </div>
      ${statusBadge(invoice.status)}
    </div>

    <div class="field-grid">
      <div class="field">
        <div class="field-label">Supplier CNPJ</div>
        <div class="field-value">${escapeHtml(formatCnpj(invoice.supplier_cnpj))}</div>
      </div>
      <div class="field">
        <div class="field-label">Total</div>
        <div class="field-value">${money(invoice.total_amount, invoice.currency || "BRL")}</div>
      </div>
      <div class="field">
        <div class="field-label">Due date</div>
        <div class="field-value">${escapeHtml(formatDate(invoice.due_date))}</div>
      </div>
    </div>

    <div class="section-title">Validation</div>
    <div class="chips">${chips(invoice.validation_errors, invoice.validation_warnings)}</div>
    ${invoice.error ? `<p class="small" style="color:var(--red)">${escapeHtml(invoice.error)}</p>` : ""}

    <div class="section-title">Extraction</div>
    <p class="small muted">Mode: ${escapeHtml(invoice.llm_mode || "—")} · text: ${escapeHtml(invoice.text_source || "—")} · confidence: ${escapeHtml(invoice.confidence ?? "—")} · attempts: ${escapeHtml(invoice.attempts ?? 0)}</p>
    <pre class="notification-box" style="margin-top:8px;overflow:auto">${escapeHtml(JSON.stringify(extraction, null, 2))}</pre>

    ${
      notifications.length
        ? `<div class="section-title">Notification</div>
           <div class="notification-box">${escapeHtml(notifications[notifications.length - 1].message || "")}<br />
           <span class="small muted">${escapeHtml(notifications[notifications.length - 1].channel)} · ${escapeHtml(notifications[notifications.length - 1].status)}</span></div>`
        : ""
    }

    <div class="section-title">Audit trail</div>
    <div class="audit">
      ${audit
        .map(
          (item) => `
          <div class="audit-item">
            <div class="audit-dot"></div>
            <div class="audit-body">
              <strong>${escapeHtml(item.action)}</strong>
              <div class="small muted">${escapeHtml(JSON.stringify(item.detail))}</div>
              <div class="audit-time">${escapeHtml(formatDateTime(item.created_at))}</div>
            </div>
          </div>`,
        )
        .join("")}
    </div>

    <div class="detail-actions">
      <a class="btn btn-ghost" href="/api/invoices/${escapeHtml(invoice.id)}/file" target="_blank" rel="noopener">Download original PDF</a>
      ${
        ["failed", "needs_review"].includes(invoice.status)
          ? `<button class="btn btn-primary" id="retry-btn" data-id="${escapeHtml(invoice.id)}">Re-process</button>`
          : ""
      }
    </div>
  `;

  const retry = $("#retry-btn");
  if (retry) {
    retry.addEventListener("click", async () => {
      try {
        await api(`/api/invoices/${retry.dataset.id}/retry`, { method: "POST" });
        toast("Invoice queued for reprocessing.", "success");
        closeModal();
        refresh();
      } catch (error) {
        toast(error.message, "error");
      }
    });
  }
}

async function openInvoice(id) {
  try {
    const data = await api(`/api/invoices/${id}`);
    state.selectedId = id;
    renderDetail(data);
    $("#detail-modal").classList.remove("hidden");
  } catch (error) {
    toast(error.message, "error");
  }
}

function closeModal() {
  state.selectedId = null;
  $("#detail-modal").classList.add("hidden");
}

async function uploadFile(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    toast("Please choose a PDF file.", "error");
    return;
  }
  const status = $("#upload-status");
  status.textContent = `Uploading ${file.name}…`;
  const form = new FormData();
  form.append("file", file);
  try {
    const result = await api("/api/invoices", { method: "POST", body: form });
    if (result.duplicate) {
      toast("Duplicate detected (same SHA-256). Existing invoice reused.", "");
      status.textContent = "Duplicate file — no new invoice was created.";
    } else {
      toast("Invoice accepted and queued.", "success");
      status.textContent = `${file.name} queued for processing.`;
    }
    await refresh();
    if (result.invoice?.id) openInvoice(result.invoice.id);
  } catch (error) {
    status.textContent = "";
    toast(error.message, "error");
  }
}

function wireEvents() {
  const input = $("#file-input");
  const dropzone = $("#dropzone");

  input.addEventListener("change", () => uploadFile(input.files[0]));

  ["dragenter", "dragover"].forEach((event) => {
    dropzone.addEventListener(event, (evt) => {
      evt.preventDefault();
      dropzone.classList.add("drag");
    });
  });
  ["dragleave", "drop"].forEach((event) => {
    dropzone.addEventListener(event, (evt) => {
      evt.preventDefault();
      dropzone.classList.remove("drag");
    });
  });
  dropzone.addEventListener("drop", (evt) => uploadFile(evt.dataTransfer.files[0]));

  $("#seed-btn").addEventListener("click", async () => {
    try {
      const result = await api("/api/demo/seed", { method: "POST" });
      toast(`${result.queued.length} sample invoice(s) enqueued.`, "success");
      setTimeout(refresh, 800);
    } catch (error) {
      toast(error.message, "error");
    }
  });

  $("#reset-btn").addEventListener("click", async () => {
    if (!window.confirm("Delete all invoices, notifications and archived files?")) return;
    try {
      await api("/api/demo/reset", { method: "POST" });
      toast("Demo data cleared.", "success");
      refresh();
    } catch (error) {
      toast(error.message, "error");
    }
  });

  $("#refresh-btn").addEventListener("click", refresh);

  $("#status-filter").addEventListener("change", (evt) => {
    state.filter = evt.target.value;
    refresh();
  });

  $("#invoice-rows").addEventListener("click", (evt) => {
    const row = evt.target.closest("tr[data-id]");
    if (row) openInvoice(row.dataset.id);
  });

  $$("[data-close]").forEach((node) => node.addEventListener("click", closeModal));
  document.addEventListener("keydown", (evt) => {
    if (evt.key === "Escape") closeModal();
  });
}

function startPolling() {
  if (state.timer) clearInterval(state.timer);
  state.timer = setInterval(() => {
    if (!document.hidden) refresh();
  }, 3000);
}

wireEvents();
refresh();
startPolling();
