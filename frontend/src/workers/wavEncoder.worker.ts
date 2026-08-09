/**
 * Off-main-thread WAV encoding.
 *
 * Downmixing to mono and writing 44 + len*2 bytes is two full passes over
 * every sample — for a 15s clip at 48kHz that's ~1.4M iterations of floating
 * point math, done synchronously right when the user hits Stop. Moving it
 * here keeps that off the thread the UI is rendering on.
 *
 * The project's single tsconfig carries the DOM lib (for `self: Window`),
 * which cannot coexist with the `webworker` lib in the same program — so
 * `self` is narrowed locally instead of pulling in `lib.webworker.d.ts`.
 */
interface EncodeMessage {
  channels: Float32Array[];
  sampleRate: number;
}

const ctx = self as unknown as {
  onmessage: ((e: MessageEvent<EncodeMessage>) => void) | null;
  postMessage: (msg: ArrayBuffer, transfer: Transferable[]) => void;
};

ctx.onmessage = (e) => {
  const { channels, sampleRate } = e.data;
  const wav = encodeWav(channels, sampleRate);
  ctx.postMessage(wav, [wav]);
};

function encodeWav(channels: Float32Array[], sampleRate: number): ArrayBuffer {
  const ch = channels.length;
  const len = channels[0]?.length ?? 0;

  const mono = new Float32Array(len);
  for (let c = 0; c < ch; c++) {
    const data = channels[c];
    if (!data) continue;
    for (let i = 0; i < len; i++) mono[i] = (mono[i] ?? 0) + (data[i] ?? 0) / ch;
  }

  const out = new DataView(new ArrayBuffer(44 + len * 2));
  const wr = (o: number, s: string) => {
    for (let i = 0; i < s.length; i++) out.setUint8(o + i, s.charCodeAt(i));
  };
  wr(0, 'RIFF'); out.setUint32(4, 36 + len * 2, true); wr(8, 'WAVE');
  wr(12, 'fmt '); out.setUint32(16, 16, true); out.setUint16(20, 1, true);
  out.setUint16(22, 1, true); out.setUint32(24, sampleRate, true); out.setUint32(28, sampleRate * 2, true);
  out.setUint16(32, 2, true); out.setUint16(34, 16, true);
  wr(36, 'data'); out.setUint32(40, len * 2, true);
  for (let i = 0; i < len; i++) {
    const s = Math.max(-1, Math.min(1, mono[i] ?? 0));
    out.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return out.buffer;
}
