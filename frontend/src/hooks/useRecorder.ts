/**
 * Mic recording.
 *
 * MediaRecorder emits webm/opus, which libsndfile on the backend cannot read,
 * so the clip is decoded and re-encoded to mono 16-bit WAV in the browser.
 *
 * Also exposes a live `level` (0..1) so the UI can draw a real meter rather
 * than a decorative pulse.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

export interface Recorder {
  recording: boolean;
  seconds: number;
  level: number;
  blob: Blob | null;
  error: string | null;
  start: () => Promise<void>;
  stop: () => void;
  reset: () => void;
}

export function useRecorder(): Recorder {
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [level, setLevel] = useState(0);
  const [blob, setBlob] = useState<Blob | null>(null);
  const [error, setError] = useState<string | null>(null);

  const mrRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunks = useRef<Blob[]>([]);
  const timer = useRef<number | null>(null);
  const raf = useRef<number | null>(null);
  const audioCtx = useRef<AudioContext | null>(null);

  /** Release everything. Safe to call repeatedly. */
  const teardown = useCallback(() => {
    if (timer.current !== null) {
      window.clearInterval(timer.current);
      timer.current = null;
    }
    if (raf.current !== null) {
      cancelAnimationFrame(raf.current);
      raf.current = null;
    }
    if (audioCtx.current) {
      void audioCtx.current.close().catch(() => {
        /* context already closed by the browser; nothing to recover */
      });
      audioCtx.current = null;
    }
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    setLevel(0);
  }, []);

  // The original hook cleaned up only inside stop(), so unmounting mid-record
  // (or switching away from the Record tab) left the mic light on.
  useEffect(() => () => teardown(), [teardown]);

  const start = useCallback(async () => {
    setError(null);
    setBlob(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      const mr = new MediaRecorder(stream);
      chunks.current = [];
      mr.ondataavailable = (e) => e.data.size && chunks.current.push(e.data);
      mr.onstop = () => {
        const webm = new Blob(chunks.current, { type: mr.mimeType || 'audio/webm' });
        webmToWav(webm)
          .then(setBlob)
          .catch((e: unknown) => setError(`Could not process the recording: ${String(e)}`));
        teardown();
      };
      mr.start();
      mrRef.current = mr;
      setRecording(true);
      setSeconds(0);
      timer.current = window.setInterval(() => setSeconds((s) => s + 1), 1000);

      // Live level meter.
      const ctx = new AudioContext();
      audioCtx.current = ctx;
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      ctx.createMediaStreamSource(stream).connect(analyser);
      const data = new Uint8Array(analyser.frequencyBinCount);
      const tick = () => {
        analyser.getByteTimeDomainData(data);
        let peak = 0;
        for (let i = 0; i < data.length; i++) {
          peak = Math.max(peak, Math.abs((data[i] ?? 128) - 128) / 128);
        }
        setLevel(peak);
        raf.current = requestAnimationFrame(tick);
      };
      raf.current = requestAnimationFrame(tick);
    } catch {
      teardown();
      setError('Microphone access was denied.');
    }
  }, [teardown]);

  const stop = useCallback(() => {
    mrRef.current?.stop(); // onstop tears down the stream + meter
    setRecording(false);
  }, []);

  const reset = useCallback(() => setBlob(null), []);

  return { recording, seconds, level, blob, error, start, stop, reset };
}

async function webmToWav(input: Blob): Promise<Blob> {
  const ctx = new AudioContext();
  try {
    const buf = await ctx.decodeAudioData(await input.arrayBuffer());
    return encodeWav(buf);
  } finally {
    await ctx.close();
  }
}

function encodeWav(buffer: AudioBuffer): Blob {
  const ch = buffer.numberOfChannels;
  const len = buffer.length;
  // down-mix to mono
  const mono = new Float32Array(len);
  for (let c = 0; c < ch; c++) {
    const data = buffer.getChannelData(c);
    for (let i = 0; i < len; i++) mono[i] = (mono[i] ?? 0) + (data[i] ?? 0) / ch;
  }
  const sr = buffer.sampleRate;
  const out = new DataView(new ArrayBuffer(44 + len * 2));
  const wr = (o: number, s: string) => {
    for (let i = 0; i < s.length; i++) out.setUint8(o + i, s.charCodeAt(i));
  };
  wr(0, 'RIFF'); out.setUint32(4, 36 + len * 2, true); wr(8, 'WAVE');
  wr(12, 'fmt '); out.setUint32(16, 16, true); out.setUint16(20, 1, true);
  out.setUint16(22, 1, true); out.setUint32(24, sr, true); out.setUint32(28, sr * 2, true);
  out.setUint16(32, 2, true); out.setUint16(34, 16, true);
  wr(36, 'data'); out.setUint32(40, len * 2, true);
  for (let i = 0; i < len; i++) {
    const s = Math.max(-1, Math.min(1, mono[i] ?? 0));
    out.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([out], { type: 'audio/wav' });
}
