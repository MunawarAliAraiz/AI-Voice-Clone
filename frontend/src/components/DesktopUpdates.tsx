import { useEffect, useRef, useState } from 'react';
import { Channel, invoke, isTauri } from '@tauri-apps/api/core';
import './DesktopUpdates.css';

interface CacheStatus { downloadState: 'available' | 'paused' | 'downloaded'; downloaded: number; total?: number; resumable: boolean }
interface UpdateInfo extends CacheStatus { version: string; notes?: string }
interface Progress { downloaded: number; total?: number }
type Stage = 'idle' | 'checking' | 'available' | 'current' | 'downloading' | 'canceling' | 'paused' | 'downloaded' | 'installing' | 'error';

export function DesktopUpdates() {
  const dialog = useRef<HTMLDialogElement>(null);
  const mounted = useRef(true);
  const startupTimer = useRef<number>();
  const [version, setVersion] = useState('');
  const [update, setUpdate] = useState<UpdateInfo | null>(null);
  const [stage, setStage] = useState<Stage>('idle');
  const [error, setError] = useState('');
  const [progress, setProgress] = useState<Progress>({ downloaded: 0 });
  const native = isTauri();
  const busy = ['checking', 'downloading', 'canceling', 'installing'].includes(stage);
  const percent = progress.total ? Math.min(100, progress.downloaded * 100 / progress.total) : undefined;

  async function check() {
    window.clearTimeout(startupTimer.current);
    if (!native || busy || stage === 'downloaded') return;
    setStage('checking'); setError('');
    try {
      const value = await invoke<UpdateInfo | null>('updater_check');
      if (!mounted.current) return;
      setUpdate(value);
      setProgress(value ? { downloaded: value.downloaded, total: value.total } : { downloaded: 0 });
      setStage(value ? value.downloadState : 'current');
    } catch (cause) {
      if (mounted.current) { setUpdate(null); setStage('error'); setError(String(cause)); }
    }
  }

  useEffect(() => {
    mounted.current = true;
    if (!native) return;
    void invoke<string>('desktop_version').then(setVersion).catch(() => {});
    startupTimer.current = window.setTimeout(() => void check(), 10000);
    const open = () => dialog.current?.showModal();
    window.addEventListener('vcs-open-updates', open);
    return () => { mounted.current = false; window.clearTimeout(startupTimer.current); window.removeEventListener('vcs-open-updates', open); };
    // Check once on launch. Download/installation always requires a click.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [native]);

  async function download() {
    window.clearTimeout(startupTimer.current);
    setStage('downloading'); setError('');
    const channel = new Channel<Progress>();
    channel.onmessage = value => { if (mounted.current) setProgress(value); };
    try {
      const result = await invoke<CacheStatus>('updater_download', { progress: channel });
      if (mounted.current) {
        setUpdate(current => current ? { ...current, ...result } : null);
        setProgress({ downloaded: result.downloaded, total: result.total });
        setStage(result.downloadState === 'downloaded' ? 'downloaded' : 'paused');
      }
    } catch (cause) { if (mounted.current) { setStage('error'); setError(String(cause)); } }
  }

  async function cancelDownload() {
    setStage('canceling');
    try { await invoke<boolean>('updater_cancel_download'); }
    catch (cause) { if (mounted.current) { setStage('downloading'); setError(String(cause)); } }
    // updater_download resolves after the active response has stopped and the
    // saved partial file is flushed. Do not offer Resume while it still writes.
  }

  async function install() {
    setStage('installing'); setError('');
    try {
      // Native preparation blocks new work atomically with checking all jobs.
      await invoke('updater_install');
    } catch (cause) { setStage('downloaded'); setError(String(cause)); }
  }

  return <>
    <button type="button" className="btn sm desktop-updates-button"
      onClick={() => { dialog.current?.showModal(); if (stage === 'idle') void check(); }}>
      {update ? 'Update available' : 'Updates'}
    </button>
    <dialog ref={dialog} className="desktop-updates-dialog" aria-labelledby="updates-heading"
      onCancel={event => { if (stage === 'installing') event.preventDefault(); }}>
      <header><h2 id="updates-heading">App updates</h2>
        <button type="button" className="btn sm" disabled={stage === 'installing'} onClick={() => dialog.current?.close()}>Close</button></header>
      <p className="hint">{version ? `Installed version ${version}. ` : ''}Updates are verified before installation. Your saved voices and history stay on this PC.</p>
      {!native && <p>Update checks are available in the installed desktop app.</p>}
      {stage === 'checking' && <p role="status">Checking for updates…</p>}
      {stage === 'current' && <p role="status">You have the latest published version.</p>}
      {update && <><p><strong>Version {update.version}</strong> is available.</p>
        {update.notes && <p className="desktop-update-notes">{update.notes}</p>}</>}
      {stage === 'downloading' && <div role="status"><p>{percent === 100 ? 'Download complete; verifying the installer…' : `Downloading update${percent == null ? '…' : ` · ${percent.toFixed(0)}%`}`}</p>
        <progress max={100} value={percent} aria-label="App update download progress" />
        <p className="hint">{(progress.downloaded / 1e6).toFixed(1)} MB downloaded</p></div>}
      {stage === 'downloaded' && <p role="status">Update downloaded and verified. Restart to apply it.</p>}
      {stage === 'canceling' && <p role="status">Stopping the update download and keeping saved bytes…</p>}
      {stage === 'paused' && <p role="status">Update download paused. {(progress.downloaded / 1e6).toFixed(1)} MB saved on this PC.
        {progress.downloaded > 0 && (update?.resumable
          ? progress.total && progress.downloaded === progress.total
            ? ' All bytes are saved. Resume verifies the installer without downloading it again.'
            : ' Resume continues from these bytes if the server confirms the same file.'
          : ' The server did not provide safe resume information; the next attempt starts a fresh download.')}</p>}
      {stage === 'downloaded' && <p className="hint">This verified installer is saved on this PC and will be reused after closing the app. A failed restart check does not require another download.</p>}
      {stage === 'installing' && <p role="status">Starting the installer. The app will restart after updating…</p>}
      {error && <p className="inline-error" role="alert">{error}</p>}
      {error && /cloud session|model setup/i.test(error) && <button type="button" className="btn sm" onClick={() => {
        dialog.current?.close(); window.dispatchEvent(new CustomEvent('vcs-open-runpod'));
      }}>Open Runpod to stop model setup</button>}
      {stage === 'error' && progress.downloaded > 0 && <p className="hint">{(progress.downloaded / 1e6).toFixed(1)} MB was transferred. Retry checks the saved file and continues from valid saved bytes when the server supports it.</p>}
      <footer>
        {native && stage !== 'downloaded' && stage !== 'installing' && <button type="button" className="btn" disabled={busy} onClick={() => void check()}>Check for updates</button>}
        {update && ['available', 'error', 'paused'].includes(stage) && <button type="button" className="btn primary" onClick={() => void download()}>{stage === 'paused' && update.resumable ? 'Resume download' : 'Download update'}</button>}
        {stage === 'downloading' && <button type="button" className="btn" onClick={() => void cancelDownload()}>Cancel download</button>}
        {stage === 'canceling' && <button type="button" className="btn" disabled>Stopping…</button>}
        {stage === 'downloaded' && <button type="button" className="btn primary" onClick={() => void install()}>Restart and update</button>}
      </footer>
    </dialog>
  </>;
}
