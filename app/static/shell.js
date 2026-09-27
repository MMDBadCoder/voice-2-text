/** App shell: sidebar session list, drawer, theme, new-session dialog. */
(function () {
  "use strict";
  const V = window.Vazhe;
  if (!V) return;
  const { api, esc, fa, toast } = V;
  const $ = (id) => document.getElementById(id);

  /* ---- theme ---------------------------------------------------------- */
  const THEME_KEY = "vazhe-theme";
  try {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved) document.documentElement.dataset.theme = saved;
  } catch (_) {}
  $("theme-btn")?.addEventListener("click", () => {
    const next =
      (document.documentElement.dataset.theme ||
        (matchMedia("(prefers-color-scheme: light)").matches
          ? "light"
          : "dark")) === "light"
        ? "dark"
        : "light";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch (_) {}
  });

  /* ---- mobile drawer --------------------------------------------------- */
  const rail = $("rail");
  let scrim;
  function closeRail() {
    rail?.classList.remove("open");
    scrim?.remove();
    scrim = null;
  }
  $("rail-toggle")?.addEventListener("click", () => {
    if (!rail) return;
    rail.classList.add("open");
    scrim = document.createElement("div");
    scrim.className = "scrim";
    scrim.addEventListener("click", closeRail);
    document.body.appendChild(scrim);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeRail();
  });

  /* ---- sidebar session list -------------------------------------------- */
  const railList = $("rail-list");
  const STATE = (j) => (j.status === "canceling" ? "canceling" : j.status);
  const here = location.pathname;

  async function loadRail() {
    if (!railList) return;
    try {
      const data = await api("/api/jobs?limit=40");
      const open = data.items.filter((j) => j.status === "open");
      const rest = data.items.filter((j) => j.status !== "open");
      let html = "";
      const row = (j) => {
        const href = j.is_session ? `/sessions/${j.id}` : `/jobs/${j.id}`;
        const cur = here === href ? ' aria-current="page"' : "";
        return `<a class="rail-item" href="${href}"${cur}><span class="dot ${esc(STATE(j))}"></span><span class="t">${esc(j.display_title)}</span></a>`;
      };
      if (open.length)
        html += `<div class="rail-section">باز</div>` + open.map(row).join("");
      if (rest.length)
        html +=
          `<div class="rail-section">بایگانی</div>` + rest.map(row).join("");
      railList.innerHTML =
        html ||
        `<p class="tiny dim" style="padding:8px 10px">هنوز جلسه‌ای ندارید</p>`;
    } catch (_) {
      /* the page itself will surface auth/network errors */
    }
  }
  loadRail();
  window.VazheRail = { reload: loadRail };

  /* ---- new session ------------------------------------------------------ */
  const dlg = $("new-session-dialog");
  document
    .querySelectorAll("#new-session-btn, [data-new-session]")
    .forEach((btn) =>
      btn.addEventListener("click", () => {
        closeRail();
        dlg?.showModal();
      }),
    );
  $("dismiss-new-session")?.addEventListener("click", () => dlg?.close());
  $("new-session-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("create-session-submit");
    const err = $("create-error");
    const title = $("session-title").value.trim();
    const tier = dlg.querySelector('[name="tier"]:checked')?.value || "";
    if (!title) {
      err.textContent = "عنوان جلسه را وارد کنید";
      return;
    }
    btn.disabled = true;
    err.textContent = "";
    try {
      const job = await api("/api/sessions", {
        method: "POST",
        body: JSON.stringify({ title, tier }),
      });
      location.href = `/sessions/${job.id}`;
    } catch (ex) {
      err.textContent = ex.message;
      btn.disabled = false;
    }
  });
})();
