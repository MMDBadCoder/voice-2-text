/* Encoding stays on the user's device; no audio is sent by this worker. */
importScripts('/static/vendor/lamejs/lame.all.js');
self.onmessage = ({data}) => {
  try {
    const encoder = new lamejs.Mp3Encoder(1, data.sampleRate, 128);
    const floats = new Float32Array(data.samples), chunks = [];
    for (let start = 0; start < floats.length; start += 1152) {
      const count = Math.min(1152, floats.length-start), pcm = new Int16Array(count);
      for (let i=0; i<count; i++) {const v=Math.max(-1,Math.min(1,floats[start+i]));pcm[i]=v<0?v*32768:v*32767;}
      const encoded=encoder.encodeBuffer(pcm);
      if(encoded.length)chunks.push(new Uint8Array(encoded));
    }
    const tail=encoder.flush();if(tail.length)chunks.push(new Uint8Array(tail));
    self.postMessage({blob:new Blob(chunks,{type:'audio/mpeg'})});
  } catch {self.postMessage({error:'ساخت MP3 انجام نشد. دوباره ضبط کنید یا یک فایل بارگذاری کنید.'});}
};
