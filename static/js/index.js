const state = { availability: null, csrfToken: null };
const $ = (selector) => document.querySelector(selector);

function showStatus(element, message, type = "error") {
  element.textContent = message;
  element.className = `form-status ${type}`;
  element.hidden = false;
}

function clearStatus(element) {
  element.textContent = "";
  element.hidden = true;
}

async function api(path, options = {}) {
  // 1. Extrai os headers customizados (se existirem) para não colidirem no spread
  const { headers, ...restOptions } = options;

  const response = await fetch(path, {
    credentials: "same-origin",
    // 2. Monta o cabeçalho base e junta com os customizados
    headers: { "Content-Type": "application/json", ...(headers || {}) },
    // 3. Espalha APENAS as outras opções (method, body, etc), preservando os headers
    ...restOptions,
  });

  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body.error || "Não foi possível concluir a solicitação.");
    error.fields = body.fields || {};
    throw error;
  }
  return body;
}

function toIsoDate(localDate = new Date()) {
  const offset = localDate.getTimezoneOffset();
  return new Date(localDate.getTime() - offset * 60000).toISOString().slice(0, 10);
}

function formatPhone(value) {
  const digits = value.replace(/\D/g, "").slice(0, 11);
  if (digits.length <= 2) return digits ? `(${digits}` : "";
  if (digits.length <= 6) return `(${digits.slice(0, 2)}) ${digits.slice(2)}`;
  if (digits.length <= 10) return `(${digits.slice(0, 2)}) ${digits.slice(2, 6)}-${digits.slice(6)}`;
  return `(${digits.slice(0, 2)}) ${digits.slice(2, 7)}-${digits.slice(7)}`;
}

function buildTimeOptions(settings) {
  const select = $("#bookingTime");
  const [startHour, startMinute] = settings.startTime.split(":").map(Number);
  const [endHour, endMinute] = settings.endTime.split(":").map(Number);
  const start = startHour * 60 + startMinute;
  const end = endHour * 60 + endMinute;
  const options = ['<option value="">Escolha um horário</option>'];
  for (let minutes = start; minutes <= end; minutes += Number(settings.slotMinutes)) {
    const value = `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
    options.push(`<option value="${value}">${value}</option>`);
  }
  select.innerHTML = options.join("");
  select.disabled = false;
}

function clearFieldErrors() {
  document.querySelectorAll(".field-error").forEach((element) => (element.textContent = ""));
  document.querySelectorAll(".form-field input, .form-field select").forEach((element) => element.removeAttribute("aria-invalid"));
}

function displayFieldErrors(errors) {
  Object.entries(errors).forEach(([name, message]) => {
    const error = document.querySelector(`#error-${CSS.escape(name)}`);
    const field = document.querySelector(`[name="${CSS.escape(name)}"]`);
    if (error) error.textContent = message;
    if (field) field.setAttribute("aria-invalid", "true");
  });
}

async function loadAvailability() {
  const data = await api("/api/availability", { headers: {} });
  state.availability = data;
  buildTimeOptions(data.settings);
  const dateInput = $("#bookingDate");
  dateInput.min = toIsoDate();
  const maxDate = new Date();
  maxDate.setDate(maxDate.getDate() + 14);
  dateInput.max = toIsoDate(maxDate);
  return data;
}

function selectedDateIsBlocked() {
  const selected = $("#bookingDate").value;
  return state.availability?.blockedDates?.some((item) => item.blockedDate === selected);
}

async function loadSupabaseImages() {
  try {
    const { images } = await api("/api/site-config");
    Object.entries(images || {}).forEach(([imageId, url]) => {
      const image = document.querySelector(`[data-supabase-image="${CSS.escape(imageId)}"]`);
      if (image && typeof url === "string" && url.startsWith("https://")) {
        image.src = url;
      }
    });
  } catch (_error) {
    // Os caminhos locais de fallback preservam a experiência caso o Storage fique indisponível.
  }
}

async function setupReservation() {
  const form = $("#reservationForm");
  const status = $("#reservationStatus");
  $("#phone").addEventListener("input", (event) => {
    event.target.value = formatPhone(event.target.value);
  });
  $("#bookingDate").addEventListener("change", () => {
    const error = $("#error-date");
    if (selectedDateIsBlocked()) {
      error.textContent = "Esta data está indisponível para reservas.";
      $("#bookingDate").setAttribute("aria-invalid", "true");
    } else {
      error.textContent = "";
      $("#bookingDate").removeAttribute("aria-invalid");
    }
  });
  try {
    await loadAvailability();
  } catch (_error) {
    showStatus(status, "Não foi possível carregar os horários. Tente atualizar a página.");
  }
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearStatus(status);
    clearFieldErrors();
    if (selectedDateIsBlocked()) {
      displayFieldErrors({ date: "Esta data está indisponível para reservas." });
      return;
    }
    const submit = form.querySelector("button[type=submit]");
    const payload = Object.fromEntries(new FormData(form));
    submit.disabled = true;
    submit.textContent = "Registrando...";
    try {
      const data = await api("/api/reservations", { method: "POST", body: JSON.stringify(payload) });
      form.reset();
      showStatus(status, `${data.message} Em breve confirmaremos pelo WhatsApp.`, "success");
    } catch (error) {
      displayFieldErrors(error.fields || {});
      showStatus(status, error.message);
    } finally {
      submit.disabled = false;
      submit.innerHTML = 'Registrar solicitação <span aria-hidden="true">→</span>';
    }
  });
}

function csrfHeaders() {
  return state.csrfToken ? { "X-CSRF-Token": state.csrfToken } : {};
}

function formatDate(value) {
  return new Intl.DateTimeFormat("pt-BR", { dateStyle: "medium" }).format(new Date(`${value}T12:00:00`));
}

function setAdminStatus(message, type = "success") {
  showStatus($("#adminStatus"), message, type);
}

function renderWeekOverview(week) {
  const range = $("#weekRange");
  const total = $("#weekTotal");
  const overview = $("#weekOverview");
  if (!overview) return;
  if (range) range.textContent = `${formatDate(week.weekStart)} a ${formatDate(week.weekEnd)}`;
  if (total) total.textContent = `${week.totalReservations}/${week.dailyReservationLimit * 7}`;
  overview.textContent = "";
  week.days.forEach((day) => {
    const item = document.createElement("article");
    item.className = "week-day";
    const title = document.createElement("strong");
    title.textContent = day.label;
    const count = document.createElement("span");
    count.textContent = `${day.reservations}/${week.dailyReservationLimit}`;
    const detail = document.createElement("small");
    detail.textContent = `${day.people} pessoa${day.people === 1 ? "" : "s"} · ${day.remaining} vaga${day.remaining === 1 ? "" : "s"}`;
    item.append(title, count, detail);
    overview.append(item);
  });
}

function renderBlockedDates(blockedDates) {
  const list = $("#blockedDatesList");
  list.textContent = "";
  if (!blockedDates.length) {
    const item = document.createElement("li");
    item.textContent = "Nenhuma data bloqueada.";
    list.append(item);
    return;
  }
  blockedDates.forEach((blocked) => {
    const item = document.createElement("li");
    const label = document.createElement("span");
    label.textContent = `${formatDate(blocked.blockedDate)}${blocked.reason ? ` — ${blocked.reason}` : ""}`;
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "Remover bloqueio";
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        await api(`/api/admin/blocked-dates/${encodeURIComponent(blocked.blockedDate)}`, { method: "DELETE", headers: csrfHeaders() });
        setAdminStatus("Bloqueio removido. A data voltou a ficar disponível.");
        await renderAdminData();
      } catch (error) {
        setAdminStatus(error.message, "error");
      } finally {
        button.disabled = false;
      }
    });
    item.append(label, button);
    list.append(item);
  });
}

async function renderAdminData() {
  const hasWeekOverview = Boolean($("#weekOverview"));
  const [reservationsData, settingsData, blockedData, weekData] = await Promise.all([
    api("/api/admin/reservations"),
    api("/api/admin/settings"),
    api("/api/admin/blocked-dates"),
    hasWeekOverview ? api("/api/admin/week") : Promise.resolve(null),
  ]);
  const table = $("#reservationsTable");
  table.textContent = "";
  if (!reservationsData.reservations.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 5;
    cell.textContent = "Nenhuma reserva registrada até o momento.";
    row.append(cell);
    table.append(row);
  } else {
    reservationsData.reservations.forEach((booking) => {
      const row = document.createElement("tr");
      [booking.customerName, formatDate(booking.bookingDate), booking.bookingTime, `${booking.partySize} pessoa${booking.partySize > 1 ? "s" : ""}`, booking.phone].forEach((value) => {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.append(cell);
      });
      table.append(row);
    });
  }
  $("#startTime").value = settingsData.settings.startTime;
  $("#endTime").value = settingsData.settings.endTime;
  $("#slotMinutes").value = String(settingsData.settings.slotMinutes);
  const dailyLimit = $("#dailyReservationLimit");
  if (dailyLimit && settingsData.settings.dailyReservationLimit !== undefined) {
    dailyLimit.value = String(settingsData.settings.dailyReservationLimit);
  }
  renderBlockedDates(blockedData.blockedDates);
  if (weekData?.week) renderWeekOverview(weekData.week);
}

async function setupAdmin() {
  if (window.location.pathname !== "/admin") return;
  const on = (element, eventName, handler) => element?.addEventListener(eventName, handler);
  const header = $(".site-header");
  if (header) header.hidden = true;
  document.querySelectorAll("main > section:not(#adminView)").forEach((section) => (section.hidden = true));
  const footer = $(".site-footer");
  const adminView = $("#adminView");
  if (footer) footer.hidden = true;
  if (!adminView) return;
  adminView.hidden = false;
  const loginView = $("#adminLogin");
  const dashboard = $("#adminDashboard");
  const loginStatus = $("#loginStatus");
  function showDashboard() {
    if (loginView) loginView.hidden = true;
    if (dashboard) dashboard.hidden = false;
    requestAnimationFrame(() => {
      renderAdminData().catch((error) => setAdminStatus(error.message, "error"));
    });
  }
  try {
    const auth = await api("/api/admin/session");
    if (auth.authenticated) { state.csrfToken = auth.csrfToken; showDashboard(); }
  } catch (_error) { /* A tela de login permanece disponível. */ }
  on($("#loginForm"), "submit", async (event) => {
    event.preventDefault(); clearStatus(loginStatus);
    const button = event.currentTarget.querySelector("button"); button.disabled = true;
    try {
      const data = await api("/api/admin/login", { method: "POST", body: JSON.stringify({ username: $("#adminUsername").value, password: $("#adminPassword").value }) });
      state.csrfToken = data.csrfToken; $("#adminPassword").value = ""; showDashboard();
    } catch (error) { showStatus(loginStatus, error.message); } finally { button.disabled = false; }
  });
  on($("#refreshReservations"), "click", () => renderAdminData().catch((error) => setAdminStatus(error.message, "error")));
  on($("#logoutButton"), "click", async () => {
    try { await api("/api/admin/logout", { method: "POST", headers: csrfHeaders() }); state.csrfToken = null; if (dashboard) dashboard.hidden = true; if (loginView) loginView.hidden = false; } catch (error) { setAdminStatus(error.message, "error"); }
  });
  on($("#scheduleForm"), "submit", async (event) => {
    event.preventDefault();
    try {
      const dailyLimit = $("#dailyReservationLimit");
      const settings = await api("/api/admin/settings", { method: "PUT", headers: csrfHeaders(), body: JSON.stringify({ startTime: $("#startTime").value, endTime: $("#endTime").value, slotMinutes: $("#slotMinutes").value, dailyReservationLimit: dailyLimit?.value || 40 }) });
      setAdminStatus(settings.message); await renderAdminData();
    } catch (error) { setAdminStatus(error.message, "error"); }
  });
  const blockedDateInput = $("#blockedDate");
  const blockReasonInput = $("#blockReason");
  const blockForm = $("#blockDateForm");
  if (blockedDateInput) blockedDateInput.min = toIsoDate();
  on(blockForm, "submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = form.querySelector("button[type=submit]");
    const blockedDate = blockedDateInput?.value || "";
    if (!blockedDate) {
      setAdminStatus("Escolha uma data antes de adicionar o bloqueio.", "error");
      blockedDateInput?.focus();
      return;
    }
    try {
      if (submit) submit.disabled = true;
      const response = await api("/api/admin/blocked-dates", { method: "POST", headers: csrfHeaders(), body: JSON.stringify({ date: blockedDate, reason: blockReasonInput?.value || "" }) });
      setAdminStatus(response.message); form.reset(); await renderAdminData();
    } catch (error) { setAdminStatus(error.message, "error"); } finally { if (submit) submit.disabled = false; }
  });
  on($("#refreshBlockedDates"), "click", () => renderAdminData().catch((error) => setAdminStatus(error.message, "error")));
}

document.addEventListener("DOMContentLoaded", () => {
  $("#currentYear").textContent = new Date().getFullYear();
  if (window.location.pathname === "/admin") {
    setupAdmin();
    return;
  }
  setupReservation();
  if ("requestIdleCallback" in window) {
    window.requestIdleCallback(() => loadSupabaseImages(), { timeout: 1200 });
  } else {
    window.setTimeout(() => loadSupabaseImages(), 0);
  }
});