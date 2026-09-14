(() => {
  'use strict';
  const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || '';
  const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fa = value => String(value).replace(/\d/g,d=>'۰۱۲۳۴۵۶۷۸۹'[d]);
  let toastTimer;
  function toast(message) { const el=document.getElementById('toast'); el.textContent=message;el.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.hidden=true,5000); }
  async function api(url, options={}) {
    const response=await fetch(url,{...options,headers:{'X-CSRF-Token':csrf(),...(options.body && !(options.body instanceof FormData) ? {'Content-Type':'application/json'}:{}),...options.headers}});
    let data;try {data=await response.json();} catch {data={};}
    if (!response.ok) {
      const error=new Error(typeof data.detail==='string' ? data.detail : 'درخواست انجام نشد؛ اطلاعات را بررسی کنید.');error.status=response.status;
      if (response.status===401 && !url.startsWith('/api/auth/')) location.href='/login';
      throw error;
    }
    if(data.csrf_token) document.querySelector('meta[name="csrf-token"]').content=data.csrf_token;
    return data;
  }
  function duration(n) {if(n==null)return '—';const s=Math.floor(n);return fa([...(s>=3600?[Math.floor(s/3600)]:[]),Math.floor(s/60)%60,s%60].map(v=>String(v).padStart(2,'0')).join(':'));}
  function date(value) {if(!value)return '';return new Intl.DateTimeFormat('fa-IR',{month:'short',day:'numeric'}).format(new Date(/[zZ]|[+-]\d\d:\d\d$/.test(value)?value:value+'Z'));}
  document.getElementById('logout-btn')?.addEventListener('click',async()=>{try{await api('/api/auth/logout',{method:'POST'});location.href='/login';}catch(e){toast(e.message);}});
  window.Vazhe={api,esc,fa,toast,csrf,duration,date};
})();
