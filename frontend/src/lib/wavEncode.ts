/**
 * Downmix + WAV byte-writing run in a worker (see workers/wavEncoder.worker.ts)
 * so encoding a multi-second clip doesn't block the main thread. `.slice()`
 * copies each channel's samples into a fresh, transferable ArrayBuffer (an
 * AudioBuffer's own channel storage can't be transferred to a worker).
 */
export function encodeWavOffMainThread(channels: Float32Array[], sampleRate: number): Promise<Blob> {
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL('../workers/wavEncoder.worker.ts', import.meta.url), {
      type: 'module',
    });
    const owned = channels.map((c) => c.slice());
    worker.onmessage = (e: MessageEvent<ArrayBuffer>) => {
      resolve(new Blob([e.data], { type: 'audio/wav' }));
      worker.terminate();
    };
    worker.onerror = (e) => {
      reject(e.error instanceof Error ? e.error : new Error('WAV encoding failed'));
      worker.terminate();
    };
    worker.postMessage(
      { channels: owned, sampleRate },
      owned.map((c) => c.buffer),
    );
  });
}

export function encodeAudioBufferAsWav(buffer: AudioBuffer): Promise<Blob> {
  const channels: Float32Array[] = [];
  for (let c = 0; c < buffer.numberOfChannels; c++) {
    channels.push(buffer.getChannelData(c));
  }
  return encodeWavOffMainThread(channels, buffer.sampleRate);
}
