/** Live panel wiring: microphone -> socket -> growing transcript. */
(function () {
  "use strict";
  if (!window.APP || window.APP.page !== "session") return;

  const { api, esc, fa, toast } = window.Vazhe;
  const $ = (id) => document.getElementById(id);
  const jobId = window.APP.jobId;

  const listEl = $("live-transcript");
  const emptyEl = $("transcript-empty");
  const countEl = $("transcript-count");
  const stateEl = $("live-state");
  const composer = $("composer");
  const latencyEl = $("live-latency");
  const meter = $("level-meter");
  const startBtn = $("live-start");
  const stopBtn = $("live-stop");
  const errEl = $("live-error");

  const BARS = 28;
  for (let i = 0; i < BARS; i++) meter.appendChild(document.createElement("i"));
  const bars = [...meter.children];

  const STATE_TEXT = {
    idle: "آمادهٔ شنیدن",
    starting: "در حال آماده‌سازی…",
    listening: "در حال شنیدن…",
    speaking: "در حال صحبت…",
    working: "در حال تبدیل…",
    stopped: "متوقف شد",
    disconnected: "ارتباط قطع شد",
    error: "خطا",
  };

  let session = null;
  const pending = new Map();   // client seq -> placeholder element
  let blocks = [];

  function setState(name) {
    stateEl.dataset.state = name;
    stateEl.className = "dot " + ({ listening:"open", speaking:"running",
      working:"queued", error:"failed", disconnected:"failed" }[name] || "");
    stateEl.textContent = STATE_TEXT[name] || name;
  }

  // Mirror the level outward from the centre: reads as a waveform, not a gauge.
  function setLevel(value) {
    const mid = (BARS - 1) / 2;
    bars.forEach((bar, i) => {
      const d = Math.abs(i - mid) / mid;               // 0 centre .. 1 edge
      const h = Math.max(0.12, value * (1 - d * 0.75) * (0.75 + Math.random() * 0.5));
      bar.style.height = `${Math.min(100, h * 100)}%`;
      bar.classList.toggle("on", h > 0.2);
    });
  }

  function words(text) {
    return (text || "").trim().split(/\s+/).filter(Boolean).length;
  }

  function refreshCount() {
    const total = blocks.reduce((n, b) => n + words(b.text), 0);
    countEl.textContent = total ? `${fa(total)} واژه` : "";
    emptyEl.hidden = blocks.length > 0 || pending.size > 0;
  }

  function blockNode(block) {
    const el = document.createElement("article");
    el.className = "blk";
    el.dataset.source = block.source;
    el.dataset.state = block.state;
    el.dataset.id = block.id;
    el.innerHTML =
      `${block.source === "file" ? `<div class="blk-tag">${esc(block.label || "فایل صوتی")}</div>` : ""}
       <div class="blk-tools">
         <button class="iconbtn danger" data-del aria-label="حذف این بخش"><svg aria-hidden="true"><use href="#i-trash"/></svg></button>
       </div>
       <div class="blk-text" contenteditable="true" spellcheck="false"></div>`;
    const textEl = el.querySelector(".blk-text");
    if (block.state === "pending") {
      textEl.innerHTML = '<span class="typing"><i></i><i></i><i></i></span>';
      textEl.removeAttribute("contenteditable");
    } else {
      textEl.textContent = block.text || "";
    }
    return el;
  }

  function appendBlock(block) {
    blocks.push(block);
    listEl.appendChild(blockNode(block));
    listEl.scrollTop = listEl.scrollHeight;
    refreshCount();
  }

  function placeholder(seq) {
    const el = document.createElement("article");
    el.className = "blk";
    el.dataset.state = "pending";
    el.innerHTML = `<div class="blk-text"><span class="typing"><i></i><i></i><i></i></span></div>`;
    listEl.appendChild(el);
    listEl.scrollTop = listEl.scrollHeight;
    pending.set(seq, el);
    emptyEl.hidden = true;
    return el;
  }

  function dropPlaceholder(seq) {
    const el = pending.get(seq);
    if (el) el.remove();
    pending.delete(seq);
    refreshCount();
  }

  // ---- edits ----------------------------------------------------------
  listEl.addEventListener("focusout", async (e) => {
    const textEl = e.target.closest(".blk-text[contenteditable]");
    if (!textEl) return;
    const wrap = textEl.closest(".blk");
    const id = wrap?.dataset.id;
    if (!id) return;
    const block = blocks.find((b) => b.id === id);
    const next = textEl.textContent.trim();
    if (!block || block.text === next) return;
    try {
      const saved = await api(`/api/sessions/${jobId}/transcript/${id}`, {
        method: "PATCH", body: JSON.stringify({ text: next }),
      });
      Object.assign(block, saved);
      refreshCount();
    } catch (err) {
      textEl.textContent = block.text;
      toast(err.message);
    }
  });

  listEl.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-del]");
    if (!btn) return;
    const wrap = btn.closest(".blk");
    const id = wrap?.dataset.id;
    if (!id) return;
    try {
      await api(`/api/sessions/${jobId}/transcript/${id}`, { method: "DELETE" });
      blocks = blocks.filter((b) => b.id !== id);
      wrap.remove();
      refreshCount();
    } catch (err) { toast(err.message); }
  });

  $("copy-live")?.addEventListener("click", async () => {
    const text = blocks.filter((b) => b.state === "done").map((b) => b.text).join("\n\n");
    if (!text) return toast("متنی برای کپی نیست.");
    try {
      await navigator.clipboard.writeText(text);
      toast("متن کپی شد.");
    } catch (_) {
      const ta = document.createElement("textarea");
      ta.value = text; document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); toast("متن کپی شد."); }
      catch (e2) { toast("کپی ممکن نشد."); }
      ta.remove();
    }
  });

  // ---- socket handlers -------------------------------------------------
  const handlers = {
    state: (name) => { if (name !== "working") setState(name); },
    level: setLevel,
    sent: (seq) => { placeholder(seq); setState("working"); },
    message: (msg) => {
      if (msg.type === "block") {
        dropPlaceholder(msg.client_seq);
        appendBlock(msg.block);
        latencyEl.textContent = `${fa(((msg.latency_ms || 0) / 1000).toFixed(1))} ثانیه`;
        setState(session && session.running ? "listening" : "stopped");
      } else if (msg.type === "empty" || msg.type === "dropped") {
        dropPlaceholder(msg.client_seq);
        if (msg.type === "dropped") toast(msg.message || "بخشی نادیده گرفته شد.");
        setState(session && session.running ? "listening" : "stopped");
      } else if (msg.type === "error") {
        dropPlaceholder(msg.client_seq);
        errEl.hidden = false;
        errEl.textContent = msg.message || "خطا در تبدیل";
        setState("error");
      }
    },
  };

  async function start() {
    errEl.textContent = ""; errEl.hidden = true;
    startBtn.disabled = true;
    try {
      session = new window.LiveSession(jobId, handlers);
      await session.start();
      startBtn.hidden = true;
      stopBtn.hidden = false;
      composer.classList.add("live");
    } catch (err) {
      session = null;
      errEl.hidden = false;
      if (err && err.code === "INSECURE_ORIGIN") {
        errEl.innerHTML =
          "مرورگر میکروفن را فقط روی <b>HTTPS</b> یا <b>localhost</b> در دسترس می‌گذارد. " +
          "برای آزمایش روی HTTP، این نشانی را در " +
          "<code>chrome://flags/#unsafely-treat-insecure-origin-as-secure</code> " +
          "به‌عنوان مبدأ امن اضافه کنید، یا از تونل SSH روی localhost استفاده کنید.";
      } else {
        const denied = err && /denied|NotAllowed|Permission/i.test(err.name + err.message);
        errEl.textContent = denied
          ? "اجازهٔ دسترسی به میکروفن داده نشد. از نوار نشانی مرورگر آن را مجاز کنید."
          : (err.message || "شروع ضبط ممکن نشد.");
      }
      setState("error");
    } finally {
      startBtn.disabled = false;
    }
  }

  async function stop() {
    if (!session) return;
    stopBtn.disabled = true;
    try { await session.stop(); } finally {
      session = null;
      stopBtn.disabled = false;
      stopBtn.hidden = true;
      startBtn.hidden = false;
      composer.classList.remove("live");
      setLevel(0);
      setState("idle");
    }
  }

  startBtn?.addEventListener("click", start);
  stopBtn?.addEventListener("click", stop);
  window.addEventListener("beforeunload", () => { if (session) session.stop(); });

  // ---- initial load ----------------------------------------------------
  (async function load() {
    try {
      const data = await api(`/api/sessions/${jobId}/transcript`);
      blocks = data.items || [];
      listEl.innerHTML = "";
      blocks.forEach((b) => listEl.appendChild(blockNode(b)));
      refreshCount();
      if (!data.live_enabled) {
        startBtn.disabled = true;
        errEl.hidden = false;
        errEl.textContent = "رونویسی زنده در این سرور فعال نیست.";
      }
    } catch (err) {
      errEl.hidden = false;
      errEl.textContent = err.message;
    }
  })();

  window.VazheLive = {
    reload: async () => {
      const data = await api(`/api/sessions/${jobId}/transcript`);
      blocks = data.items || [];
      listEl.innerHTML = "";
      blocks.forEach((b) => listEl.appendChild(blockNode(b)));
      refreshCount();
    },
  };
})();
