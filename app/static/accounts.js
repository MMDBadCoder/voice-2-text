(() => {
  'use strict';
  const {api,esc,fa,toast}=window.Vazhe;
  const $=id=>document.getElementById(id);
  const post=(url,body)=>api(url,{method:'POST',body:JSON.stringify(body)});
  const redirect=user=>location.href=user.status==='approved'?'/':'/pending';
  if($('auth-card')){
    const mode=$('auth-card').dataset.mode;let method='password',details={},challenge=null,deadline=0,resendAt=0,timer;
    const error=message=>{$('auth-error').textContent=message;$('auth-error').hidden=!message;};
    function tick(){const remaining=Math.max(0,Math.ceil((deadline-Date.now())/1000));$('code-expiry').textContent=remaining?`اعتبار کد: ${fa(Math.floor(remaining/60))}:${fa(String(remaining%60).padStart(2,'0'))}`:'کد منقضی شد؛ کد جدید درخواست کنید.';$('resend-code').disabled=Date.now()<resendAt;$('resend-code').textContent=Date.now()<resendAt?`ارسال دوباره تا ${fa(Math.ceil((resendAt-Date.now())/1000))} ثانیه`:'دریافت کد جدید';}
    async function requestCode(){
      challenge=await post('/api/auth/challenges',{phone:details.phone,purpose:mode==='login'?'login':mode});
      $('credentials-form').hidden=true;$('verification-form').hidden=false;
      $('bale-link').href=challenge.bot_url;$('bale-command').textContent=challenge.bot_command;
      $('delivery-label').textContent=challenge.sent?'کد تأیید در بله ارسال شد':'شمارهٔ خود را در بله تأیید کنید';
      $('verification-code').value='';deadline=Date.now()+challenge.expires_in*1000;resendAt=Date.now()+60000;clearInterval(timer);timer=setInterval(tick,1000);tick();$('verification-code').focus();
    }
    document.querySelectorAll('[data-method]').forEach(button=>button.addEventListener('click',()=>{method=button.dataset.method;document.querySelectorAll('[data-method]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));$('password-field').hidden=method==='bale';$('password').required=method==='password';$('auth-submit').textContent=method==='bale'?'دریافت کد در بله':'ورود به حساب';error('');}));
    $('credentials-form').addEventListener('submit',async e=>{e.preventDefault();error('');const button=$('auth-submit');button.disabled=true;details=Object.fromEntries(new FormData(e.target));try{if(mode==='login'&&method==='password'){const data=await post('/api/auth/login/password',details);redirect(data.user);}else await requestCode();}catch(err){error(err.message);}finally{button.disabled=false;}});
    $('verification-form').addEventListener('submit',async e=>{e.preventDefault();error('');$('verify-submit').disabled=true;try{const fields=Object.fromEntries(new FormData(e.target));const payload={...details,...fields,challenge_id:challenge.challenge_id};const url=mode==='signup'?'/api/auth/signup':mode==='reset'?'/api/auth/password/reset':'/api/auth/login/bale';const data=await post(url,payload);if(mode==='reset'){location.href='/login?reset=1';}else redirect(data.user);}catch(err){error(err.message);}finally{$('verify-submit').disabled=false;}});
    $('resend-code').addEventListener('click',async()=>{error('');$('resend-code').disabled=true;try{await requestCode();}catch(err){resendAt=Date.now()+60000;error(err.message);}});
    $('back-to-phone').addEventListener('click',()=>{clearInterval(timer);$('verification-form').hidden=true;$('credentials-form').hidden=false;error('');$('phone').focus();});
    if(new URLSearchParams(location.search).has('reset'))toast('رمز جدید ذخیره شد؛ وارد حساب شوید.');
  }
  if($('approval-refresh')){
    let timer;async function check(){clearTimeout(timer);try{const data=await api('/api/auth/me');if(data.user.status==='approved')return location.href='/';$('approval-message').textContent=data.user.status==='pending'?'هنوز در انتظار تأیید مدیر هستید.':'دسترسی حساب شما متوقف شده است.';}catch(e){if(e.status===401)return location.href='/login';$('approval-message').textContent=e.message;}timer=setTimeout(check,15000);}
    $('approval-refresh').addEventListener('click',check);check();
  }
  $('change-password-form')?.addEventListener('submit',async e=>{e.preventDefault();const b=e.target.querySelector('button');b.disabled=true;try{await post('/api/auth/password/change',Object.fromEntries(new FormData(e.target)));e.target.reset();$('password-message').textContent='';toast('رمز عبور تغییر کرد.');}catch(err){$('password-message').textContent=err.message;}finally{b.disabled=false;}});
  if($('admin-users')){
    let offset=0,version=0,timer;const states={pending:'در انتظار تأیید',approved:'فعال',suspended:'متوقف‌شده'};
    async function load(){const current=++version;clearTimeout(timer);try{const data=await api('/api/admin/users?'+new URLSearchParams({search:$('user-search').value,status:$('user-filter').value,offset}));if(version!==current)return;$('user-count').textContent=`${fa(data.total)} کاربر`;$('admin-users').innerHTML=data.items.length?data.items.map(u=>`<article class="admin-user"><div class="avatar">${esc(u.full_name.slice(0,1))}</div><div class="user-info"><strong>${esc(u.full_name)}</strong><span dir="ltr">${esc(u.phone)}</span></div><span class="status ${u.status==='approved'?'done':'queued'}">${states[u.status]}</span><div class="actions">${u.is_admin?'<span class="small muted">مدیر سامانه</span>':`${u.status!=='approved'?`<button class="btn primary small" data-user="${u.id}" data-status="approved">تأیید دسترسی</button>`:''}${u.status!=='suspended'?`<button class="btn small danger" data-user="${u.id}" data-status="suspended">توقف دسترسی</button>`:''}`}</div></article>`).join(''):'<p class="empty">کاربری با این مشخصات پیدا نشد.</p>';$('admin-more').hidden=offset+data.items.length>=data.total;$('admin-error').textContent='';}catch(e){$('admin-error').textContent=e.message;}}
    $('admin-users').addEventListener('click',async e=>{const b=e.target.closest('[data-user]');if(!b)return;if(b.dataset.status==='suspended'&&!confirm('دسترسی این کاربر متوقف شود؟ پردازش‌های فعال او نیز متوقف می‌شوند.'))return;b.disabled=true;try{await post(`/api/admin/users/${b.dataset.user}/status`,{status:b.dataset.status});await load();}catch(err){toast(err.message);b.disabled=false;}});
    $('user-search').addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(()=>{offset=0;load();},250);});$('user-filter').addEventListener('change',()=>{offset=0;load();});$('admin-more').addEventListener('click',()=>{offset+=100;load();});load();
  }
})();
