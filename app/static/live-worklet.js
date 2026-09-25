/**
 * Microphone tap. Runs on the audio thread, so it must stay trivial: copy the
 * frame out and post it. All buffering, resampling and voice detection happen
 * on the main thread, where a slow frame cannot cause an audio glitch.
 */
class MicTap extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0];
    if (input && input[0] && input[0].length) {
      // The engine reuses this buffer, so a copy is mandatory.
      this.port.postMessage(new Float32Array(input[0]));
    }
    return true;
  }
}
registerProcessor("mic-tap", MicTap);
