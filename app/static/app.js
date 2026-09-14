(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const fa = value => String(value).replace(/\d/g, d => '۰۱۲۳۴۵۶۷۸۹'[d]);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const labels = {open:'باز',queued:'در صف',running:'در حال تبدیل',done:'آماده',failed:'ناموفق',canceled:'متوقف شده',canceling:'در حال توقف'};
  const state = j => j.stage === 'canceling' ? 'canceling' : j.status;
  const busy = j => ['queued','running','canceling'].includes(state(j));
  const percent = j => Math.min(j.status === 'done' ? 100 : 99, Math.max(0, Math.floor((j.progress || 0) * 100)));
  const icon = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 10v4m4-7v10m4-14v18m4-15v12m4-8v4"/></svg>';
  function duration(seconds) {
    if (seconds == null) return '';
    const n = Math.max(0, Math.floor(seconds));
    return fa([...(n >= 3600 ? [Math.floor(n / 3600)] : []),Math.floor(n / 60) % 60,n % 60].map(v => String(v).padStart(2,'0')).join(':'));
  }
  function when(iso) {
    if (!iso) return '';
    // SQLite returns naive UTC dates.
    return new Intl.DateTimeFormat('fa-IR', {month:'short',day:'numeric',year:'numeric'}).format(new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : iso + 'Z'));
  }
  async function api(url, options) {
    const response = await fetch(url, {...options, headers: {...options?.headers, "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]')?.content || ""}});
    if (!response.ok) {
      let message = 'ارتباط برقرار نشد. دوباره تلاش کنید.';
      try { const body = await response.json(); if (typeof body.detail === 'string') message = body.detail; } catch {}
      throw new Error(message);
    }
    return response.json();
  }
  let toastTimer;
  function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 4500); }
  function confirmDelete() {
    const dialog = $('confirm-dialog');
    return new Promise(resolve => { dialog.returnValue = 'cancel'; dialog.addEventListener('close', () => resolve(dialog.returnValue === 'delete'), {once:true}); dialog.showModal(); });
  }
  async function remove(id) { if (!await confirmDelete()) return false; await api(`/api/jobs/${id}`, {method:'DELETE'}); return true; }
  function progress(element, bar, value, indefinite = false) {
    element.classList.toggle('indeterminate', indefinite);
    if (indefinite) element.removeAttribute('aria-valuenow'); else element.setAttribute('aria-valuenow', value);
    bar.style.width = `${value}%`;
  }

  function initIndex() {
    const form = $('upload-form'), input = $('file-input'), submit = $('submit-btn');
    let uploading = false, selected = null;
    function message(text, error = false) { $('upload-status').textContent = text; $('upload-status').className = 'upload-status' + (error ? ' err' : ''); }
    function pick(file) {
      if (uploading || !file) return;
      const allowed = input.accept.split(',').map(v => v.trim());
      const extension = '.' + file.name.split('.').pop().toLowerCase();
      if (!allowed.includes(extension) || !file.size || file.size > Number(form.dataset.maxMb) * 1024 * 1024) {
        selected = null; input.value = ''; submit.disabled = true; $('dz-file').hidden = true; $('dropzone').classList.remove('has-file');
        message('یک فایل صوتی معتبر، در محدودهٔ حجم مجاز انتخاب کنید.', true); return;
      }
      selected = file; $('dz-file').textContent = `${file.name} · ${fa((file.size / 1024 / 1024).toFixed(1))} مگابایت`;
      $('dz-file').hidden = false; $('dropzone').classList.add('has-file'); submit.disabled = false; message('');
    }
    input.addEventListener('change', () => pick(input.files[0]));
    for (const event of ['dragenter','dragover']) $('dropzone').addEventListener(event, e => {e.preventDefault(); if (!uploading) $('dropzone').classList.add('dragover');});
    for (const event of ['dragleave','drop']) $('dropzone').addEventListener(event, e => {e.preventDefault(); $('dropzone').classList.remove('dragover');});
    $('dropzone').addEventListener('drop', e => pick(e.dataTransfer.files[0]));
    form.addEventListener('submit', e => {
      e.preventDefault(); if (!selected || uploading) return;
      const data = new FormData(form); data.set('file', selected);
      const xhr = new XMLHttpRequest(); xhr.open('POST','/api/jobs');
      uploading = true;
      const controls = [...form.elements]; controls.forEach(el => el.disabled = true);
      $('upload-progress').hidden = false; progress($('upload-progress'), $('upload-bar'), 0); message('در حال بارگذاری…');
      xhr.upload.addEventListener('progress', ev => {
        if (!ev.lengthComputable) return;
        const pct = Math.floor(ev.loaded / ev.total * 100);
        progress($('upload-progress'), $('upload-bar'), pct, pct === 100);
        message(pct < 100 ? `بارگذاری فایل · ${fa(pct)}٪` : 'فایل دریافت شد؛ در حال آماده‌سازی…');
      });
      function finish() { uploading = false; controls.forEach(el => el.disabled = false); $('upload-progress').hidden = true; submit.disabled = !selected; }
      xhr.addEventListener('load', () => {
        finish();
        if (xhr.status === 201) {
          form.reset(); selected = null; input.value = ''; $('dz-file').hidden = true; $('dropzone').classList.remove('has-file'); submit.disabled = true;
          message('ضبط اضافه شد. می‌توانید صدای بعدی را انتخاب کنید.');
          filter = ''; search = ''; $('search').value = ''; limit = 20; updateTabs(); refresh(); toast('ضبط شما به کتابخانه اضافه شد.');
        } else { let msg = 'بارگذاری انجام نشد. دوباره تلاش کنید.'; try { const d = JSON.parse(xhr.responseText); if (typeof d.detail === 'string') msg = d.detail; } catch {} message(msg, true); }
      });
      xhr.addEventListener('error', () => {finish(); message('ارتباط قطع شد. اتصال را بررسی کنید و دوباره تلاش کنید.', true);});
      xhr.send(data);
    });
    let filter = '', search = '', limit = 20, timer, requestNumber = 0;
    function updateTabs() { document.querySelectorAll('[data-filter]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.filter === filter))); }
    function row(j) {
      const st = state(j), pct = percent(j);
      return `<article class="job-row" data-id="${esc(j.id)}"><span class="recording-icon">${icon}</span><div class="job-content"><a class="job-name" href="/jobs/${esc(j.id)}">${esc(j.display_title || j.title || j.original_name)}</a><div class="job-sub"><span>${esc(when(j.created_at))}</span>${j.duration_sec ? `<span dir="ltr">${duration(j.duration_sec)}</span>` : ''}${st === 'running' ? `<span>${fa(pct)}٪</span>` : ''}</div>${st === 'running' ? `<div class="job-progress" role="progressbar" aria-label="پیشرفت تبدیل" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"><div class="bar" style="width:${pct}%"></div></div>` : ''}</div><div class="job-end"><span class="status ${st}">${labels[st]}</span>${['running','queued'].includes(st) ? '<button class="icon-button js-cancel" aria-label="توقف تبدیل" title="توقف تبدیل"><svg viewBox="0 0 24 24"><rect x="7" y="7" width="10" height="10" rx="2"/></svg></button>' : ''}<a class="icon-button" href="/jobs/${esc(j.id)}" aria-label="باز کردن ضبط" title="باز کردن ضبط"><svg viewBox="0 0 24 24"><path d="m14 7-5 5 5 5"/></svg></a></div></article>`;
    }
    async function refresh() {
      clearTimeout(timer); const version = ++requestNumber;
      try {
        const query = new URLSearchParams({limit, search, ...(filter ? {status:filter} : {})});
        const data = await api('/api/jobs?' + query);
        if (version !== requestNumber) return;
        $('list-error').hidden = true; $('jobs').setAttribute('aria-busy','false');
        // Preserve keyboard focus when a polling update replaces a row.
        const active = document.activeElement, activeRow = active?.closest('.job-row');
        const focusId = activeRow?.dataset.id;
        const focusKind = active?.classList.contains('js-cancel') ? '.js-cancel' : active?.classList.contains('job-name') ? '.job-name' : '.job-end a';
        $('jobs').innerHTML = data.items.length ? data.items.map(row).join('') : `<div class="empty">${icon}<h3>${search || filter ? 'ضبطی پیدا نشد' : 'اولین واژه از اینجا شروع می‌شود'}</h3><p>${search || filter ? 'جست‌وجو یا فیلتر را تغییر دهید.' : 'یک فایل صوتی اضافه کنید؛ متن آن اینجا منتظر شماست.'}</p></div>`;
        if (focusId) document.querySelector(`[data-id="${CSS.escape(focusId)}"] ${focusKind}`)?.focus({preventScroll:true});
        $('queue-summary').textContent = fa(data.total);
        $('more-btn').hidden = data.items.length >= data.total || limit >= 200;
        timer = setTimeout(refresh, data.items.some(busy) ? 1500 : 8000);
      } catch (err) {
        if (version !== requestNumber) return;
        $('list-error').hidden = false; $('list-error').textContent = err.message;
        $('jobs').setAttribute('aria-busy','false'); timer = setTimeout(refresh, 5000);
      }
    }
    let debounce;
    $('search').addEventListener('input', () => { clearTimeout(debounce); clearTimeout(timer); requestNumber++; debounce = setTimeout(() => {search = $('search').value; limit = 20; refresh();}, 250); });
    document.querySelectorAll('[data-filter]').forEach(b => b.addEventListener('click', () => {filter = b.dataset.filter; limit = 20; updateTabs(); refresh();}));
    $('more-btn').addEventListener('click', () => {limit += 20; refresh();});
    $('refresh-btn').addEventListener('click', refresh);
    $('jobs').addEventListener('click', async e => {
      const button = e.target.closest('.js-cancel'); if (!button) return;
      button.disabled = true;
      try { await api(`/api/jobs/${button.closest('.job-row').dataset.id}/cancel`, {method:'POST'}); await refresh(); }
      catch (err) {toast(err.message); button.disabled = false;}
    });
    refresh();
  }

  function initJob(id) {
    let loaded = false, timer, segments = [];
    const player = $('audio-player');
    async function loadTranscript() {
      if (loaded) return;
      try {
        const data = await api(`/api/jobs/${id}/segments`); segments = data.segments; loaded = true;
        $('stub-note').hidden = !data.is_stub;
        $('transcript').innerHTML = segments.length ? segments.map(s => `${s.speaker ? `<div class="speaker-head">${esc(s.speaker)}</div>` : ''}<div class="seg"><button class="seg-time" data-time="${Number(s.start)}" aria-label="پخش از ${duration(s.start)}">${duration(s.start)}</button><span class="seg-text">${esc(s.text)}</span></div>`).join('') : '<p class="empty">گفتاری در این فایل تشخیص داده نشد.</p>';
      } catch { $('transcript').innerHTML = '<p class="inline-error">دریافت متن انجام نشد؛ در حال تلاش دوباره…</p>'; }
    }
    async function poll() {
      clearTimeout(timer);
      try {
        const j = await api(`/api/jobs/${id}`), st = state(j), pct = percent(j);
        $('connection-error').hidden = true;
        $('job-status').textContent = labels[st]; $('job-status').className = `status ${st}`;
        $('job-title').textContent = j.display_title;
        $('job-meta').innerHTML = [`${when(j.created_at)}`, j.duration_sec ? `مدت صدا ${duration(j.duration_sec)}` : '',j.tier === 'accurate' ? 'تبدیل دقیق' : 'تبدیل سریع'].filter(Boolean).map(v => `<span>${esc(v)}</span>`).join('');
        $('cancel-btn').hidden = !['queued','running'].includes(st);
        $('retry-btn').hidden = !['failed','canceled'].includes(st);
        $('processing-panel').hidden = !busy(j);
        const stage = {assembling:'در حال آماده‌سازی فایل‌های جلسه',probing:'در حال آماده‌سازی صدا',transcribing:'صدای شما به واژه تبدیل می‌شود',diarizing:'در حال تشخیص گویندگان'};
        $('job-stage').textContent = st === 'canceling' ? 'در حال توقف پردازش…' : st === 'queued' ? 'نوبت ضبط شما می‌رسد' : stage[j.stage] || 'در حال تبدیل به متن';
        $('progress-hint').textContent = st === 'canceling' ? 'پس از توقف کامل، امکان شروع دوباره دارید.' : st === 'queued' ? (j.queue_position ? `نوبت ${fa(j.queue_position)} در صف · می‌توانید به کارهای دیگرتان برسید.` : 'پس از پایان ضبط‌های قبلی، تبدیل شروع می‌شود.') : 'می‌توانید صفحه را ببندید؛ متن در کتابخانه ذخیره می‌شود.';
        $('job-percent').textContent = st === 'running' && pct > 0 ? `${fa(pct)}٪` : '';
        progress($('job-progress'), $('job-bar'), pct, st !== 'running' || pct === 0);
        $('job-error').hidden = st !== 'failed';
        if (st === 'failed') $('job-error').innerHTML = `<strong>تبدیل این ضبط انجام نشد.</strong><p>دوباره تلاش کنید یا فایل صوتی دیگری انتخاب کنید.</p>${j.error ? `<details><summary>جزئیات خطا</summary><pre>${esc(j.error)}</pre></details>` : ''}`;
        $('transcript-card').hidden = st !== 'done';
        $('audio-wrap').hidden = j.audio_deleted;
        if (st === 'done') await loadTranscript();
        if (busy(j) || (st === 'done' && !loaded)) timer = setTimeout(poll, 1200);
      } catch { $('connection-error').hidden = false; timer = setTimeout(poll, 4000); }
    }
    $('cancel-btn').addEventListener('click', async () => { $('cancel-btn').disabled = true; try {await api(`/api/jobs/${id}/cancel`, {method:'POST'}); await poll();} catch(err) {toast(err.message);} finally {$('cancel-btn').disabled = false;} });
    $('retry-btn').addEventListener('click', async () => { $('retry-btn').disabled = true; try {await api(`/api/jobs/${id}/retry`, {method:'POST'}); loaded = false; await poll();} catch(err) {toast(err.message);} finally {$('retry-btn').disabled = false;} });
    $('delete-btn').addEventListener('click', async () => {try {if (await remove(id)) location.href = '/';} catch(err) {toast(err.message);} });
    $('ts-toggle').addEventListener('change', e => $('transcript').classList.toggle('hide-ts', !e.target.checked));
    $('transcript').addEventListener('click', e => { const button = e.target.closest('[data-time]'); if (button && !$('audio-wrap').hidden) {player.currentTime = Number(button.dataset.time); player.play().catch(() => toast('پخش صوت ممکن نشد.'));} });
    player.addEventListener('error', () => { $('audio-wrap').hidden = true; });
    $('copy-btn').addEventListener('click', async () => {
      const text = segments.map(s => s.text).join('\n');
      if (!loaded || !text) return toast('متنی برای کپی موجود نیست.');
      try {
        if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(text);
        else {const area = document.createElement('textarea'); area.value = text; area.style.cssText = 'position:fixed;top:0;left:0;opacity:0'; document.body.append(area); area.select(); const ok = document.execCommand('copy'); area.remove(); $('copy-btn').focus(); if (!ok) throw new Error();}
        toast('متن کپی شد.');
      } catch {toast('متن را انتخاب کنید و کپی کنید.');}
    });
    poll();
  }
  if (window.APP?.page === 'index') initIndex();
  if (window.APP?.page === 'job') initJob(window.APP.jobId);
})();
