/**
 * Client-side audio extraction for voice enrollment uploads.
 *
 * Decodes an uploaded audio/video file in-browser via `AudioContext.decodeAudioData`
 * and slices out a clip, so only a small WAV is sent to the backend instead of the
 * full source file.
 *
 * `decodeAudioData` handles mp4/webm/most audio containers in current browsers but
 * throws on mkv/avi/flv (no demuxer). Callers MUST treat a thrown error here as a
 * signal to fall back to the existing server-side ffmpeg upload path — visibly,
 * per golden rule 5, never silently.
 */
import { encodeWavOffMainThread } from './wavEncode';

export const MAX_CLIENT_CLIP_SEC = 30;

export async function decodeMediaFile(file: File): Promise<AudioBuffer> {
  const ctx = new AudioContext();
  try {
    return await ctx.decodeAudioData(await file.arrayBuffer());
  } finally {
    await ctx.close();
  }
}

/** Slices `[startSec, endSec)` out of `buffer` and encodes it as a WAV blob. */
export async function extractWavClip(
  buffer: AudioBuffer,
  startSec: number,
  endSec: number,
): Promise<Blob> {
  const sr = buffer.sampleRate;
  const startFrame = Math.max(0, Math.floor(startSec * sr));
  const endFrame = Math.min(buffer.length, Math.ceil(endSec * sr));
  if (endFrame <= startFrame) {
    throw new Error('Selected clip range is empty.');
  }

  const channels: Float32Array[] = [];
  for (let c = 0; c < buffer.numberOfChannels; c++) {
    channels.push(buffer.getChannelData(c).subarray(startFrame, endFrame));
  }
  return encodeWavOffMainThread(channels, sr);
}
