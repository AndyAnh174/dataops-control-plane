const jsonRequest = async (url, options = {}) => {
  const response = await fetch(url, {
    credentials: "same-origin",
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      message = typeof body.detail === "string" ? body.detail : message;
    } catch (_) {
      // Keep the status-based message when the response is not JSON.
    }
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
};

const showError = (container, error) => {
  const element = container.querySelector(".form-error");
  if (!element) return;
  element.textContent = error.message;
  element.hidden = false;
};

document.querySelectorAll("[data-toggle]").forEach((button) => {
  button.addEventListener("click", () => {
    const target = document.getElementById(button.dataset.toggle);
    target.hidden = !target.hidden;
  });
});

document.querySelectorAll("[data-api-form]").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(form));
    const kind = form.dataset.apiForm;
    let url;
    if (kind === "bootstrap") url = "/api/v1/auth/bootstrap";
    if (kind === "login") url = "/api/v1/auth/login";
    if (kind === "project") url = `/api/v1/workspaces/${form.dataset.workspaceId}/projects`;
    try {
      await jsonRequest(url, { method: "POST", body: JSON.stringify(values) });
      window.location.assign("/app");
    } catch (error) {
      showError(form, error);
    }
  });
});

document.querySelector("[data-action='logout']")?.addEventListener("click", async () => {
  await jsonRequest("/api/v1/auth/logout", { method: "POST" });
  window.location.assign("/login");
});

document.querySelectorAll("[data-create-token]").forEach((button) => {
  button.addEventListener("click", async () => {
    const dialog = document.getElementById("token-dialog");
    try {
      const token = await jsonRequest(`/api/v1/projects/${button.dataset.createToken}/tokens`, {
        method: "POST",
        body: JSON.stringify({
          name: `agent-${new Date().toISOString().slice(0, 10)}`,
          scopes: ["runs:write", "logs:write", "reports:write", "verification:write"],
          expires_in_days: 90,
        }),
      });
      document.getElementById("token-secret").textContent = token.token;
      dialog.showModal();
    } catch (error) {
      showError(dialog, error);
      dialog.showModal();
    }
  });
});

document.querySelector("[data-action='close-token']")?.addEventListener("click", () => {
  document.getElementById("token-dialog").close();
  window.location.reload();
});
