const jsonRequest = async (url, options = {}) => {
  const response = await fetch(url, {
    credentials: "same-origin",
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) {
    let message = "Request failed (" + response.status + ")";
    try {
      const body = await response.json();
      if (typeof body.detail === "string") message = body.detail;
      else if (Array.isArray(body.detail)) {
        message = body.detail.map((item) => item.msg).filter(Boolean).join(". ") || message;
      }
    } catch (_) {
      // Keep the HTTP status when no readable error is available.
    }
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
};

const showError = (container, error) => {
  const element = container?.querySelector(".form-error");
  if (!element) return;
  element.textContent = error.message;
  element.hidden = false;
};

let toastTimer;
const notify = (message) => {
  const toast = document.getElementById("app-toast");
  if (!toast) return;
  clearTimeout(toastTimer);
  toast.textContent = message;
  toast.hidden = false;
  toastTimer = setTimeout(() => { toast.hidden = true; }, 4500);
};

const confirmAction = ({ title, message, confirmLabel, expected, inputLabel }) => {
  const dialog = document.getElementById("confirm-dialog");
  if (!dialog || dialog.open) return Promise.resolve(null);
  const form = document.getElementById("confirm-form");
  const input = document.getElementById("confirm-input");
  const submit = document.getElementById("confirm-submit");
  document.getElementById("confirm-title").textContent = title;
  document.getElementById("confirm-description").textContent = message;
  document.getElementById("confirm-input-caption").textContent = inputLabel || "";
  document.getElementById("confirm-input-label").hidden = !inputLabel;
  input.value = "";
  input.required = Boolean(inputLabel);
  submit.textContent = confirmLabel;
  submit.disabled = Boolean(expected);
  dialog.returnValue = "";
  return new Promise((resolve) => {
    const onInput = () => { submit.disabled = Boolean(expected && input.value !== expected); };
    const onSubmit = (event) => {
      event.preventDefault();
      if (expected && input.value !== expected) return;
      dialog.close("confirmed");
    };
    const onClose = () => {
      form.removeEventListener("submit", onSubmit);
      input.removeEventListener("input", onInput);
      resolve(dialog.returnValue === "confirmed" ? input.value || true : null);
    };
    input.addEventListener("input", onInput);
    form.addEventListener("submit", onSubmit);
    dialog.addEventListener("close", onClose, { once: true });
    dialog.showModal();
    if (inputLabel) input.focus();
    else dialog.querySelector("[data-close-confirm]").focus();
  });
};
document.querySelectorAll("[data-close-confirm]").forEach((button) => {
  button.addEventListener("click", () => document.getElementById("confirm-dialog").close("canceled"));
});

document.querySelectorAll("[data-toggle]").forEach((button) => {
  button.addEventListener("click", () => {
    const target = document.getElementById(button.dataset.toggle);
    target.hidden = !target.hidden;
    document.querySelectorAll("[data-toggle]").forEach((control) => {
      if (control.dataset.toggle === target.id) control.setAttribute("aria-expanded", String(!target.hidden));
    });
    if (!target.hidden) target.querySelector("input")?.focus();
  });
});

document.querySelectorAll("[data-api-form]").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(form));
    const kind = form.dataset.apiForm;
    const submit = form.querySelector("[type='submit']");
    submit.disabled = true;
    form.setAttribute("aria-busy", "true");
    form.querySelector(".form-error").hidden = true;
    const urls = {
      bootstrap: "/api/v1/auth/bootstrap",
      login: "/api/v1/auth/login",
      project: "/api/v1/workspaces/" + form.dataset.workspaceId + "/projects",
      member: "/api/v1/workspaces/" + form.dataset.workspaceId + "/members",
    };
    try {
      await jsonRequest(urls[kind], { method: "POST", body: JSON.stringify(values) });
      window.location.assign("/app");
    } catch (error) {
      showError(form, error);
    } finally {
      submit.disabled = false;
      form.removeAttribute("aria-busy");
    }
  });
});

document.querySelectorAll("[data-member-role]").forEach((select) => {
  select.addEventListener("change", async () => {
    select.disabled = true;
    try {
      await jsonRequest("/api/v1/workspaces/" + select.dataset.workspaceId + "/members/" + select.dataset.memberRole,
        { method: "PATCH", body: JSON.stringify({ role: select.value }) });
      window.location.reload();
    } catch (error) {
      select.disabled = false;
      showError(select.closest("[data-member-container]"), error);
    }
  });
});

document.querySelectorAll("[data-remove-member]").forEach((button) => {
  button.addEventListener("click", async () => {
    const confirmed = await confirmAction({
      title: "Remove workspace member",
      message: "Remove " + button.dataset.memberEmail + " from this workspace?",
      confirmLabel: "Remove member",
    });
    if (!confirmed) return;
    button.disabled = true;
    try {
      await jsonRequest("/api/v1/workspaces/" + button.dataset.workspaceId + "/members/" + button.dataset.removeMember, { method: "DELETE" });
      window.location.reload();
    } catch (error) {
      button.disabled = false;
      showError(button.closest("[data-member-container]"), error);
    }
  });
});

document.querySelector("[data-action='logout']")?.addEventListener("click", async () => {
  try {
    await jsonRequest("/api/v1/auth/logout", { method: "POST" });
    window.location.assign("/login");
  } catch (error) {
    notify(error.message);
  }
});

document.querySelectorAll("[data-create-token]").forEach((button) => {
  button.addEventListener("click", async () => {
    const dialog = document.getElementById("token-dialog");
    button.disabled = true;
    document.getElementById("token-secret").textContent = "";
    dialog.querySelector(".form-error").hidden = true;
    try {
      const token = await jsonRequest("/api/v1/projects/" + button.dataset.createToken + "/tokens", {
        method: "POST",
        body: JSON.stringify({
          name: "agent-" + new Date().toISOString().replace(/[-:T.Z]/g, ""),
          scopes: ["runs:write", "logs:write", "reports:write", "verification:write"],
          expires_in_days: 90,
        }),
      });
      document.getElementById("token-secret").textContent = token.token;
      dialog.showModal();
    } catch (error) {
      showError(dialog, error);
      dialog.showModal();
    } finally {
      button.disabled = false;
    }
  });
});
document.querySelectorAll("[data-action='close-token']").forEach((button) => {
  button.addEventListener("click", () => document.getElementById("token-dialog").close());
});
document.getElementById("token-dialog")?.addEventListener("close", () => {
  document.getElementById("token-secret").textContent = "";
  window.location.reload();
});

document.querySelectorAll("[data-delete-project]").forEach((button) => {
  button.addEventListener("click", async () => {
    const projectRef = button.dataset.projectRef;
    const confirmation = await confirmAction({
      title: "Remove project",
      message: "This removes the project and revokes its Agent tokens. Run history is retained.",
      confirmLabel: "Remove project",
      expected: projectRef,
      inputLabel: "Type " + projectRef + " to confirm",
    });
    if (confirmation === null) return;
    button.disabled = true;
    try {
      await jsonRequest("/api/v1/workspaces/" + button.dataset.workspaceId + "/projects/" + button.dataset.deleteProject,
        { method: "DELETE", body: JSON.stringify({ confirm_project_ref: confirmation }) });
      window.location.assign("/app");
    } catch (error) {
      button.disabled = false;
      showError(button.closest("[data-project-container]"), error);
    }
  });
});

document.querySelectorAll("[data-copy-target]").forEach((button) => {
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(document.getElementById(button.dataset.copyTarget).textContent);
      notify("Copied to clipboard");
    } catch (_) {
      notify("Copy failed. Select the text and copy it manually.");
    }
  });
});

document.querySelectorAll("[data-incident-action]").forEach((button) => {
  button.addEventListener("click", async () => {
    const control = document.getElementById("recovery-control");
    const action = button.dataset.incidentAction;
    let url = "/api/v1/incidents/" + control.dataset.incidentId + "/recovery-plans";
    const options = { method: "POST" };
    if (action !== "create-plan") url += "/" + control.dataset.planId + "/" + action;
    if (action === "approve") options.body = JSON.stringify({ actor: "web-session" });
    if (action === "reject") {
      const reason = await confirmAction({
        title: "Reject recovery plan", message: "Add a reason for the recovery audit trail.",
        confirmLabel: "Reject plan", inputLabel: "Reason",
      });
      if (!reason) return;
      options.body = JSON.stringify({ actor: "web-session", reason });
    }
    button.disabled = true;
    try {
      await jsonRequest(url, options);
      window.location.reload();
    } catch (error) {
      button.disabled = false;
      showError(control, error);
    }
  });
});

document.querySelectorAll("[data-table-controls]").forEach((controls) => {
  const table = document.getElementById(controls.dataset.tableControls);
  const rows = Array.from(table.querySelectorAll("[data-search-row]"));
  const search = controls.querySelector("[data-search]");
  const status = controls.querySelector("[data-status-filter]");
  const count = controls.querySelector("[data-filter-count]");
  const empty = table.querySelector("[data-filter-empty]");
  const update = () => {
    const query = search.value.trim().toLowerCase();
    let visible = 0;
    rows.forEach((row) => {
      row.hidden = !row.textContent.toLowerCase().includes(query) || Boolean(status?.value && row.dataset.status !== status.value);
      if (!row.hidden) visible += 1;
    });
    if (empty) empty.hidden = visible > 0 || rows.length === 0;
    if (count) count.textContent = visible + " of " + rows.length + (table.id === "run-logs" ? " entries" : " runs");
  };
  search.addEventListener("input", update);
  status?.addEventListener("change", update);
});

document.querySelectorAll("[data-local-time]").forEach((element) => {
  const raw = element.getAttribute("datetime");
  const date = new Date(/[zZ]$|[+-]\d{2}:\d{2}$/.test(raw) ? raw : raw + "Z");
  if (Number.isNaN(date.getTime())) return;
  element.textContent = new Intl.DateTimeFormat("en-GB", {
    day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
    ...(element.closest(".log-line") ? { second: "2-digit" } : {}),
  }).format(date);
  element.title = date.toLocaleString(undefined, { timeZoneName: "short" });
});
document.addEventListener("click", (event) => {
  document.querySelectorAll(".row-menu[open]").forEach((menu) => {
    if (!menu.contains(event.target)) menu.removeAttribute("open");
  });
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") document.querySelectorAll(".row-menu[open]").forEach((menu) => menu.removeAttribute("open"));
});
