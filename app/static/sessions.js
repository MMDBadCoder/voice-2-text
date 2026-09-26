/** Sessions: the index grid, and the clip/close controls on a session page. */
(function () {
  "use strict";
  const V = window.Vazhe; if (!V) return;
  const { api, esc, fa, toast, duration, date } = V;
  const $ = (id) => document.getElementById(id);
  const cfg = window.APP || {};

  const LABEL = { open:"باز", queued:"در صف", running:"در حال تبدیل",
                  done:"آماده", failed:"ناموفق", canceled:"متوقف", canceling:"در حال توقف" };
  const state = (j) => (j.stage === "canceling" ? "canceling" : j.status);

  /* ═══════════════════════════════════════════════════ index ══════════ */
  if (cfg.page === "workspace") {
    let offset = 0, filter = "", timer, debounce, version = 0;

    async function load() {
      clearTimeout(timer);
      const v = ++version;
      try {
        const data = await api("/api/jobs?" + new URLSearchParams({
          limit: 24, offset, status: filter, search: $("session-search")?.value || "",
        }));
        if (v !== version) return;
        $("session-count").textContent = `${fa(data.total)} جلسه`;
        $("sessions-prev").hidden = !offset;
        $("sessions-next").hidden = offset + data.items.length >= data.total;

        $("session-list").innerHTML = data.items.length
          ? data.items.map((j) => {
              const st = state(j);
              const href = j.is_session ? `/sessions/${j.id}` : `/jobs/${j.id}`;
              const bits = [esc(date(j.created_at))];
              if (j.clip_count) bits.push(`${fa(j.clip_count)} فایل`);
              if (j.duration_sec) bits.push(`<span dir="ltr">${duration(j.duration_sec)}</span>`);
              bits.push(j.tier === "accurate" ? "دقیق" : "سریع");
              return `<a class="card" href="${href}">
                <div class="card-t"><strong>${esc(j.display_title)}</strong>
                  <span class="dot ${st}">${LABEL[st] || st}</span></div>
                <div class="card-m">${bits.join('<span class="sep">·</span>')}</div>
                ${st === "running" ? `<div class="bar"><i style="width:${Math.min(99, Math.floor(j.progress*100))}%"></i></div>` : ""}
              </a>`;
            }).join("")
          : `<div class="blank" style="grid-column:1/-1"><h3>${
               filter || $("session-search")?.value ? "چیزی پیدا نشد" : "هنوز جلسه‌ای ندارید"
             }</h3><p>با «جلسهٔ جدید» شروع کنید.</p></div>`;

        $("library-error").textContent = "";
        const busy = data.items.some((j) => ["queued","running","canceling"].includes(state(j)));
        timer = setTimeout(load, busy ? 2500 : 12000);
      } catch (e) {
        $("library-error").textContent = e.message;
        timer = setTimeout(load, 6000);
      }
    }

    document.querySelectorAll("[data-session-filter]").forEach((b) =>
      b.addEventListener("click", () => {
        document.querySelectorAll("[data-session-filter]").forEach((x) =>
          x.setAttribute("aria-pressed", String(x === b)));
        filter = b.dataset.sessionFilter; offset = 0; load();
      }));
    $("session-search")?.addEventListener("input", () => {
      clearTimeout(debounce); debounce = setTimeout(() => { offset = 0; load(); }, 250);
    });
    $("sessions-prev")?.addEventListener("click", () => { offset = Math.max(0, offset - 24); load(); });
    $("sessions-next")?.addEventListener("click", () => { offset += 24; load(); });
    load();
  }

  /* ═════════════════════════════════════════════════ session ══════════ */
  if (cfg.page === "session") {
    const jobId = cfg.jobId;
    let job = null, timer;

    function isOpen() { return job && job.status === "open"; }

    function paint() {
      const st = state(job);
      const statusEl = $("session-status");
      statusEl.className = `dot ${st}`;
      statusEl.textContent = LABEL[st] || st;

      const bits = [];
      if (job.clip_count) bits.push(`${fa(job.clip_count)} فایل صوتی`);
      if (job.duration_sec) bits.push(`<span dir="ltr">${duration(job.duration_sec)}</span>`);
      bits.push(job.tier === "accurate" ? "تبدیل دقیق" : "تبدیل سریع");
      bits.push(esc(date(job.created_at)));
      $("session-summary").innerHTML = bits.join('<span class="sep">·</span>');

      $("view-transcript").hidden = job.status !== "done";
      $("close-session").hidden = !isOpen();
      $("reopen-session").hidden = job.status !== "canceled" && job.status !== "failed";
      $("composer").hidden = !isOpen();
      if (!isOpen()) document.querySelector(".doc")?.style.setProperty("padding-bottom", "40px");
    }

    async function refresh() {
      clearTimeout(timer);
      try {
        const data = await api(`/api/sessions/${jobId}/clips`);
        job = data.job;
        paint();
        renderClips(data.items);
        const busy = ["queued","running","canceling"].includes(state(job));
        timer = setTimeout(refresh, busy ? 2500 : 15000);
      } catch (e) {
        $("clip-error").textContent = e.message;
        timer = setTimeout(refresh, 6000);
      }
    }

    function renderClips(items) {
      const sec = $("clip-section");
      sec.hidden = !items.length;
      $("clip-list").innerHTML = items.map((c, i) => `
        <div class="clip" data-id="${esc(c.id)}">
          <span class="clip-n">${fa(i + 1)}</span>
          <div class="clip-b"><strong>${esc(c.original_name)}</strong>
            <span class="tiny dim" dir="ltr">${duration(c.duration_sec)}</span></div>
          <audio controls preload="none" src="/api/sessions/${jobId}/clips/${c.id}/audio"></audio>
          ${isOpen() ? `<button class="iconbtn danger js-delclip" aria-label="حذف فایل"><svg><use href="#i-trash"/></svg></button>` : ""}
        </div>`).join("");
    }

    $("clip-list")?.addEventListener("click", async (e) => {
      const btn = e.target.closest(".js-delclip");
      if (!btn) return;
      const id = btn.closest(".clip").dataset.id;
      try { await api(`/api/sessions/${jobId}/clips/${id}`, { method: "DELETE" }); refresh(); }
      catch (err) { toast(err.message); }
    });

    /* ---- upload ------------------------------------------------------- */
    const input = $("clip-input"), drop = $("clip-dropzone");
    const wrap = $("upload-status-wrap"), statusEl = $("clip-upload-status");
    const barWrap = $("clip-upload-progress"), bar = $("clip-upload-bar");
    let queue = [];

    function setStatus(msg, bad) {
      wrap.hidden = !msg;
      statusEl.textContent = msg || "";
      statusEl.style.color = bad ? "var(--danger)" : "";
    }

    function upload(file) {
      return new Promise((resolve, reject) => {
        const fd = new FormData(); fd.append("file", file);
        const xhr = new XMLHttpRequest();
        xhr.open("POST", `/api/sessions/${jobId}/clips`);
        xhr.setRequestHeader("X-CSRF-Token", V.csrf());
        barWrap.hidden = false;
        xhr.upload.addEventListener("progress", (ev) => {
          if (ev.lengthComputable) bar.style.width = (ev.loaded / ev.total) * 100 + "%";
        });
        xhr.addEventListener("load", () => {
          bar.style.width = "0"; barWrap.hidden = true;
          if (xhr.status === 201) return resolve();
          let m = `خطا ${xhr.status}`;
          try { m = JSON.parse(xhr.responseText).detail || m; } catch (_) {}
          reject(new Error(m));
        });
        xhr.addEventListener("error", () => { barWrap.hidden = true; reject(new Error("ارتباط قطع شد")); });
        xhr.send(fd);
      });
    }

    async function runQueue() {
      $("retry-uploads").hidden = true;
      $("discard-uploads").hidden = true;
      while (queue.length) {
        const file = queue[0];
        const max = (cfg.maxMb || 500) * 1024 * 1024;
        if (file.size > max) {
          setStatus(`«${file.name}» بزرگ‌تر از حد مجاز است`, true);
          queue.shift(); continue;
        }
        setStatus(`در حال افزودن «${file.name}»…`);
        try {
          await upload(file);
          queue.shift();
        } catch (e) {
          setStatus(e.message, true);
          $("retry-uploads").hidden = false;
          $("discard-uploads").hidden = false;
          return;
        }
        refresh();
        window.VazheLive?.reload?.();
      }
      setStatus("");
    }

    input?.addEventListener("change", () => {
      queue = queue.concat([...input.files]);
      input.value = "";
      runQueue();
    });
    $("retry-uploads")?.addEventListener("click", runQueue);
    $("discard-uploads")?.addEventListener("click", () => { queue = []; setStatus(""); 
      $("retry-uploads").hidden = true; $("discard-uploads").hidden = true; });

    ["dragenter","dragover"].forEach((ev) => document.addEventListener(ev, (e) => {
      if (!isOpen()) return; e.preventDefault(); drop?.classList.add("dragover"); }));
    ["dragleave","drop"].forEach((ev) => document.addEventListener(ev, (e) => {
      e.preventDefault(); drop?.classList.remove("dragover"); }));
    document.addEventListener("drop", (e) => {
      if (!isOpen() || !e.dataTransfer?.files?.length) return;
      queue = queue.concat([...e.dataTransfer.files]); runQueue();
    });

    /* ---- close / reopen / delete -------------------------------------- */
    const closeDlg = $("close-dialog");
    $("close-session")?.addEventListener("click", () => {
      $("close-confirm-summary").textContent =
        job.clip_count ? `${fa(job.clip_count)} فایل صوتی به یک متن پیوسته تبدیل می‌شود.`
                       : "متن زنده به‌عنوان متن نهایی ذخیره می‌شود.";
      closeDlg.showModal();
    });
    $("dismiss-close")?.addEventListener("click", () => closeDlg.close());
    $("confirm-close")?.addEventListener("click", async () => {
      $("confirm-close").disabled = true;
      try { await api(`/api/sessions/${jobId}/close`, { method: "POST" }); location.href = `/jobs/${jobId}`; }
      catch (e) { toast(e.message); $("confirm-close").disabled = false; closeDlg.close(); }
    });
    $("reopen-session")?.addEventListener("click", async () => {
      try { await api(`/api/sessions/${jobId}/reopen`, { method: "POST" }); refresh(); }
      catch (e) { toast(e.message); }
    });
    $("delete-session")?.addEventListener("click", () => {
      const d = $("confirm-dialog");
      d.showModal();
      d.addEventListener("close", async function once() {
        d.removeEventListener("close", once);
        if (d.returnValue !== "delete") return;
        try { await api(`/api/jobs/${jobId}`, { method: "DELETE" }); location.href = "/"; }
        catch (e) { toast(e.message); }
      });
    });

    refresh();
  }
})();
