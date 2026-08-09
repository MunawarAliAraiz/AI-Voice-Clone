/**
 * Mic recording.
 *
 * MediaRecorder emits webm/opus, which libsndfile on the backend cannot read,
 * so the clip is decoded and re-encoded to mono 16-bit WAV in the browser.
 *
 * The live level meter is deliberately NOT React state: it ticks on every
 * `requestAnimationFrame` (~60/s), and `setState` at that rate re-renders
 * every consumer of this hook's return value on every frame. `onLevel` is an
 * imperative escape hatch — callers write it straight into the DOM (a CSS
 * custom property, typically) instead of going through React.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

export interface Recorder {
  recording: boolean;
  seconds: number;
  blob: Blob | null;
  error: string | null;
  start: () => Promise<void>;
  stop: () => void;
  reset: () => void;
}

export function useRecorder(onLevel?: (level: number) => void): Recorder {
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [blob, setBlob] = useState<Blob | null>(null);
  const [error, setError] = useState<string | null>(null);

  const mrRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunks = useRef<Blob[]>([]);
  const timer = useRef<number | null>(null);
  const raf = useRef<number | null>(null);
  const audioCtx = useRef<AudioContext | null>(null);
  const onLevelRef = useRef(onLevel);
  onLevelRef.current = onLevel;

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
    onLevelRef.current?.(0);
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
        onLevelRef.current?.(peak);
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

  return { recording, seconds, blob, error, start, stop, reset };
}

async function webmToWav(input: Blob): Promise<Blob> {
  const ctx = new AudioContext();
  try {
    const buf = await ctx.decodeAudioData(await input.arrayBuffer());
    return await encodeWavOffMainThread(buf);
  } finally {
    await ctx.close();
  }
}

/**
 * Downmix + WAV byte-writing run in a worker (see wavEncoder.worker.ts) so
 * the ~1.4M-iteration encode for a 15s clip doesn't block the main thread
 * the instant the user hits Stop. `.slice()` copies each channel's samples
 * out of the AudioBuffer's own storage (which itself can't be transferred)
 * into a fresh, transferable ArrayBuffer.
 */
function encodeWavOffMainThread(buffer: AudioBuffer): Promise<Blob> {
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL('../workers/wavEncoder.worker.ts', import.meta.url), {
      type: 'module',
    });
    const channels: Float32Array[] = [];
    for (let c = 0; c < buffer.numberOfChannels; c++) {
      channels.push(buffer.getChannelData(c).slice());
    }
    worker.onmessage = (e: MessageEvent<ArrayBuffer>) => {
      resolve(new Blob([e.data], { type: 'audio/wav' }));
      worker.terminate();
    };
    worker.onerror = (e) => {
      reject(e.error instanceof Error ? e.error : new Error('WAV encoding failed'));
      worker.terminate();
    };
    worker.postMessage(
      { channels, sampleRate: buffer.sampleRate },
      channels.map((c) => c.buffer),
    );
  });
}
