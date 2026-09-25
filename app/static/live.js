/**
 * رونویسی زنده — live microphone transcription.
 *
 * The browser decides where one utterance ends. That is the whole trick: we
 * watch microphone energy, wait for a real pause, and ship exactly one complete
 * utterance. Whisper is not a streaming model -- feeding it a sliding window
 * would re-decode the same audio repeatedly and change words under the reader.
 * Cutting at a pause means each piece is decoded once and never rewritten.
 */
(function () {
  "use strict";

  const TARGET_RATE = 16000;
  const FRAME_MS = 20;
  const PREROLL_MS = 300;    // keep audio from just before onset, or words clip
  const SILENCE_MS = 800;     // pause that ends an utterance
  const MIN_UTTER_MS = 2500;  // below this we hold and merge -- see carryover
  const MAX_UTTER_MS = 18000; // ship anyway, so latency stays bounded
  const CARRY_MAX_MS = 6000;  // a lone short word still gets sent eventually
  const ONSET_FRAMES = 2;
  const ABS_FLOOR = 0.004;   // below this it is a quiet room, not speech

  const $ = (id) => document.getElementById(id);
  const FA = "۰۱۲۳۴۵۶۷۸۹";
  const fa = (s) => String(s).replace(/[0-9]/g, (d) => FA[+d]);

  class LiveSession {
    constructor(jobId, handlers) {
      this.jobId = jobId;
      this.on = handlers;
      this.ws = null;
      this.ctx = null;
      this.stream = null;
      this.node = null;
      this.seq = 0;
      this.running = false;

      this.acc = [];           // resampled 16k float frames awaiting analysis
      this.accLen = 0;
      this.ratio = 1;
      this.carry = 0;

      this.preroll = [];       // ring of recent frames, used as utterance onset
      this.prerollFrames = Math.ceil(PREROLL_MS / FRAME_MS);
      this.speech = [];        // frames of the utterance being captured
      this.carry = [];         // too-short speech, held to merge with the next
      this.carryIdle = 0;
      this.speaking = false;
      this.onsetRun = 0;
      this.silenceRun = 0;
      this.noiseFloor = 0.01;
      this.level = 0;
    }

    async start() {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });

      this.ctx = new (window.AudioContext || window.webkitAudioContext)();
      if (this.ctx.state === "suspended") await this.ctx.resume();
      this.ratio = this.ctx.sampleRate / TARGET_RATE;

      await this.openSocket();

      const source = this.ctx.createMediaStreamSource(this.stream);
      try {
        await this.ctx.audioWorklet.addModule("/static/live-worklet.js");
        this.node = new AudioWorkletNode(this.ctx, "mic-tap");
        this.node.port.onmessage = (e) => this.ingest(e.data);
        source.connect(this.node);
        // Worklets need a sink to be pulled; a muted gain keeps it silent.
        const sink = this.ctx.createGain();
        sink.gain.value = 0;
        this.node.connect(sink).connect(this.ctx.destination);
      } catch (err) {
        // Older Safari / insecure contexts without AudioWorklet.
        const proc = this.ctx.createScriptProcessor(4096, 1, 1);
        proc.onaudioprocess = (e) => this.ingest(new Float32Array(e.inputBuffer.getChannelData(0)));
        source.connect(proc);
        proc.connect(this.ctx.destination);
        this.node = proc;
      }

      this.running = true;
      this.on.state("listening");
    }

    openSocket() {
      return new Promise((resolve, reject) => {
        const scheme = location.protocol === "https:" ? "wss" : "ws";
        this.ws = new WebSocket(`${scheme}://${location.host}/ws/sessions/${this.jobId}/live`);
        this.ws.binaryType = "arraybuffer";
        let settled = false;

        this.ws.addEventListener("message", (e) => {
          let msg;
          try { msg = JSON.parse(e.data); } catch (_) { return; }
          if (msg.type === "ready") { settled = true; resolve(); return; }
          if (msg.type === "error" && msg.fatal) {
            settled = true;
            reject(new Error(msg.message || "اتصال برقرار نشد"));
            return;
          }
          this.on.message(msg);
        });
        this.ws.addEventListener("error", () => {
          if (!settled) { settled = true; reject(new Error("اتصال به سرور برقرار نشد")); }
        });
        this.ws.addEventListener("close", () => {
          if (!settled) { settled = true; reject(new Error("اتصال بسته شد")); }
          else if (this.running) this.on.state("disconnected");
        });
      });
    }

    /** Resample the native-rate frame to 16 kHz and hand it to the detector. */
    ingest(frame) {
      const out = [];
      let i = this.carry;
      while (i < frame.length) {
        out.push(frame[Math.floor(i)]);
        i += this.ratio;
      }
      this.carry = i - frame.length;

      this.acc.push(Float32Array.from(out));
      this.accLen += out.length;

      const frameLen = Math.round(TARGET_RATE * FRAME_MS / 1000);
      while (this.accLen >= frameLen) {
        const chunk = this.take(frameLen);
        this.analyse(chunk);
      }
    }

    take(n) {
      const out = new Float32Array(n);
      let filled = 0;
      while (filled < n) {
        const head = this.acc[0];
        const need = n - filled;
        if (head.length <= need) {
          out.set(head, filled);
          filled += head.length;
          this.acc.shift();
        } else {
          out.set(head.subarray(0, need), filled);
          this.acc[0] = head.subarray(need);
          filled = n;
        }
      }
      this.accLen -= n;
      return out;
    }

    analyse(frame) {
      let sum = 0;
      for (let i = 0; i < frame.length; i++) sum += frame[i] * frame[i];
      const rms = Math.sqrt(sum / frame.length);

      // Track the quiet floor so a noisy room raises the bar instead of
      // transcribing its own hum forever.
      if (!this.speaking) {
        this.noiseFloor = rms < this.noiseFloor
          ? this.noiseFloor * 0.9 + rms * 0.1
          : this.noiseFloor * 0.995 + rms * 0.005;
      }
      const threshold = Math.max(ABS_FLOOR, this.noiseFloor * 2.5);
      const voiced = rms > threshold;

      this.level = this.level * 0.7 + Math.min(1, rms / 0.15) * 0.3;
      this.on.level(this.level);

      if (!this.speaking) {
        this.preroll.push(frame);
        if (this.preroll.length > this.prerollFrames) this.preroll.shift();
        if (voiced) {
          if (++this.onsetRun >= ONSET_FRAMES) {
            this.speaking = true;
            this.silenceRun = 0;
            // Anything held back from a too-short utterance leads this one, so
            // no speech is ever dropped for being brief.
            this.speech = this.carry.concat(this.preroll);
            this.carry = [];
            this.carryIdle = 0;
            this.preroll = [];
            this.on.state("speaking");
          }
        } else {
          this.onsetRun = 0;
          if (this.carry.length) {
            this.carryIdle += FRAME_MS;
            if (this.carryIdle >= CARRY_MAX_MS) {
              this.speech = this.carry;
              this.carry = [];
              this.carryIdle = 0;
              this.ship();   // a single short word deserves its own block
            }
          }
        }
        return;
      }

      this.speech.push(frame);
      this.silenceRun = voiced ? 0 : this.silenceRun + 1;

      const lenMs = this.speech.length * FRAME_MS;
      if (this.silenceRun * FRAME_MS >= SILENCE_MS) {
        if (lenMs - SILENCE_MS >= MIN_UTTER_MS) {
          this.flush();
        } else {
          // Too short to be worth a whole decode. Whisper pads every call to a
          // 30 s window, so a 1 s chunk costs almost what a 10 s chunk costs --
          // hold it and let it ride along with the next utterance.
          this.carry = this.speech;
          this.carryIdle = 0;
          this.reset();
        }
      } else if (lenMs >= MAX_UTTER_MS) {
        this.flush();
      }
    }

    reset() {
      this.speaking = false;
      this.onsetRun = 0;
      this.silenceRun = 0;
      this.speech = [];
      this.on.state("listening");
    }

    flush() {
      this.ship();
    }

    ship() {
      const frames = this.speech;
      this.reset();
      if (!frames.length || !this.ws || this.ws.readyState !== WebSocket.OPEN) return;

      let total = 0;
      for (const f of frames) total += f.length;
      const pcm = new Int16Array(total);
      let o = 0;
      for (const f of frames) {
        for (let i = 0; i < f.length; i++) {
          const v = Math.max(-1, Math.min(1, f[i]));
          pcm[o++] = v < 0 ? v * 0x8000 : v * 0x7fff;
        }
      }

      const seq = ++this.seq;
      const payload = new Uint8Array(4 + pcm.byteLength);
      new DataView(payload.buffer).setUint32(0, seq, true);
      payload.set(new Uint8Array(pcm.buffer), 4);
      this.ws.send(payload);
      this.on.sent(seq, Math.round(total / TARGET_RATE * 1000));
    }

    async stop() {
      this.running = false;
      // On stop, send whatever is buffered regardless of length -- the user is
      // done talking, so there is no "next utterance" to merge into.
      if (this.carry.length) { this.speech = this.carry.concat(this.speech); this.carry = []; }
      if (this.speech.length) this.ship();
      if (this.node) { try { this.node.disconnect(); } catch (_) {} }
      if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
      if (this.ctx) { try { await this.ctx.close(); } catch (_) {} }
      if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.close();
      this.on.state("stopped");
    }
  }

  window.LiveSession = LiveSession;
  window.liveFa = fa;
})();
