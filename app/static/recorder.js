(() => {
  'use strict';
  if(window.APP?.page!=='session')return;
  const $=id=>document.getElementById(id),{duration,toast}=window.Vazhe;
  let recorder,stream,timer,chunks=[],localBlob=null,localURL=null,encoding=false,generation=0;
  function tracksOff(){stream?.getTracks().forEach(t=>t.stop());stream=null;clearInterval(timer);$('record-indicator').classList.remove('recording');}
  function discard(){if(localURL)URL.revokeObjectURL(localURL);localBlob=null;localURL=null;$('record-preview').hidden=true;$('local-audio').removeAttribute('src');window.SessionEditor.setBusy('local-recording',false);$('record-start').disabled=false;$('record-state').textContent='آمادهٔ ضبط';}
  function encodeMP3(blob){return new Promise(async(resolve,reject)=>{
    let context,worker;
    try{
      const AudioContext=window.AudioContext||window.webkitAudioContext;
      context=new AudioContext();const audio=await context.decodeAudioData(await blob.arrayBuffer());
      const samples=new Float32Array(audio.length);for(let c=0;c<audio.numberOfChannels;c++){const channel=audio.getChannelData(c);for(let i=0;i<samples.length;i++)samples[i]+=channel[i]/audio.numberOfChannels;}
      const sampleRate=audio.sampleRate;await context.close();context=null;
      worker=new Worker('/static/mp3-worker.js');worker.onmessage=({data})=>{worker.terminate();data.error?reject(new Error(data.error)):resolve(data.blob);};worker.onerror=()=>{worker.terminate();reject(new Error('رمزگذاری MP3 انجام نشد.'));};worker.postMessage({samples:samples.buffer,sampleRate},[samples.buffer]);
    }catch(e){context?.close();worker?.terminate();reject(e);}
  });}
  $('record-start').addEventListener('click',async()=>{
    if(!window.SessionEditor.isOpen())return;
    $('record-error').textContent='';
    if(!window.isSecureContext||!navigator.mediaDevices?.getUserMedia){$('record-error').textContent='برای دسترسی به میکروفن، برنامه را با نشانی HTTPS یا localhost باز کنید. بارگذاری فایل همچنان در دسترس است.';return;}
    if(!window.MediaRecorder){$('record-error').textContent='این مرورگر ضبط صدا را پشتیبانی نمی‌کند. فایل صوتی بارگذاری کنید.';return;}
    if(localBlob){toast('ابتدا ضبط قبلی را ذخیره یا حذف کنید.');return;}
    const own=++generation;$('record-start').disabled=true;$('record-start').hidden=true;$('record-stop').hidden=false;window.SessionEditor.setBusy('recording',true);$('record-state').textContent='در انتظار اجازهٔ میکروفن…';
    try{
      const acquired=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true},video:false});
      if(own!==generation){acquired.getTracks().forEach(t=>t.stop());return;}stream=acquired;
      const mime=['audio/webm;codecs=opus','audio/ogg;codecs=opus','audio/mp4'].find(t=>MediaRecorder.isTypeSupported(t));
      recorder=new MediaRecorder(stream,mime?{mimeType:mime,audioBitsPerSecond:128000}:{});chunks=[];
      recorder.ondataavailable=e=>{if(e.data.size)chunks.push(e.data);};
      recorder.onerror=()=>{$('record-error').textContent='ضبط صدا قطع شد. فایل محلی را بررسی کنید.';tracksOff();if(recorder.state!=='inactive')recorder.stop();};
      recorder.onstop=async()=>{
        tracksOff();$('record-stop').hidden=true;$('record-start').hidden=false;encoding=true;$('record-state').textContent='در حال ساخت MP3 روی دستگاه شما…';
        try{localBlob=await encodeMP3(new Blob(chunks,{type:recorder.mimeType}));chunks=[];localURL=URL.createObjectURL(localBlob);$('local-audio').src=localURL;$('download-recording').href=localURL;$('record-preview').hidden=false;$('record-name').value=`بخش ${new Date().toLocaleTimeString('fa-IR',{hour:'2-digit',minute:'2-digit'})}`;$('record-state').textContent='ضبط آماده است؛ هنوز ذخیره نشده';window.SessionEditor.setBusy('local-recording',true);}catch(e){$('record-error').textContent=e.message||'ضبط انجام نشد.';$('record-start').disabled=false;}finally{encoding=false;window.SessionEditor.setBusy('recording',false);}
      };
      recorder.start(1000);const start=performance.now();$('record-indicator').classList.add('recording');$('record-state').textContent='در حال ضبط…';$('record-timer').textContent=duration(0);
      timer=setInterval(()=>{const elapsed=(performance.now()-start)/1000;$('record-timer').textContent=duration(elapsed);if(elapsed>=600&&recorder.state==='recording')recorder.stop();},250);
    }catch(e){tracksOff();$('record-stop').hidden=true;$('record-start').hidden=false;$('record-start').disabled=false;window.SessionEditor.setBusy('recording',false);$('record-state').textContent='ضبط شروع نشد';$('record-error').textContent=e.name==='NotAllowedError'?'اجازهٔ میکروفن داده نشد. از تنظیمات مرورگر دسترسی را فعال کنید.':e.name==='NotFoundError'?'میکروفنی پیدا نشد.':'دسترسی به میکروفن ممکن نشد؛ فایل صوتی بارگذاری کنید.';}
  });
  $('record-stop').addEventListener('click',()=>{if(recorder?.state==='recording')recorder.stop();else{generation++;tracksOff();window.SessionEditor.setBusy('recording',false);$('record-start').hidden=false;$('record-start').disabled=false;$('record-stop').hidden=true;$('record-state').textContent='آمادهٔ ضبط';}});
  $('discard-recording').addEventListener('click',()=>{if(!encoding)discard();});
  $('save-recording').addEventListener('click',async()=>{if(!localBlob)return;const b=$('save-recording');b.disabled=true;$('discard-recording').disabled=true;try{const name=($('record-name').value.trim()||'ضبط مرورگر').replace(/[\\/]/g,'-');await window.SessionEditor.saveRecording(new File([localBlob],name+'.mp3',{type:'audio/mpeg'}));discard();toast('ضبط به جلسه اضافه شد.');}catch(e){$('record-error').textContent=e.message;}finally{b.disabled=false;$('discard-recording').disabled=false;}});
  window.addEventListener('pagehide',tracksOff);
})();
