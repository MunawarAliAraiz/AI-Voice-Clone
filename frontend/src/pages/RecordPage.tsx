import { useState, useRef, useCallback, useEffect } from 'react';
import { voiceApi } from '../services/api';
import type { VoiceProfile } from '../types';
import './RecordPage.css';

export default function RecordPage() {
  // Recording state
  const [isRecording, setIsRecording] = useState(false);
  const [audioBlob, setAudioBlob] = useState<Blob | null>(null);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [duration, setDuration] = useState(0);
  const mediaRecorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval>>();

  // Form state
  const [profileName, setProfileName] = useState('');
  const [transcript, setTranscript] = useState('');
  const [language, setLanguage] = useState('en');
  const [saving, setSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [error, setError] = useState('');

  // Mode & File Upload
  const [activeMode, setActiveMode] = useState<'record' | 'upload'>('record');
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileUpload = (file: File) => {
    if (!file.type.startsWith('audio/') && !/\.(wav|mp3|m4a|ogg|webm)$/i.test(file.name)) {
      setError('Please upload a valid audio file (.wav, .mp3, .m4a, .ogg, .webm).');
      return;
    }
    setError('');
    setAudioBlob(file);
    setAudioUrl(URL.createObjectURL(file));
    if (!profileName) {
      const defaultName = file.name.replace(/\.[^/.]+$/, '');
      setProfileName(defaultName);
    }
    setSaveSuccess(false);

    const tempAudio = new Audio(URL.createObjectURL(file));
    tempAudio.onloadedmetadata = () => {
      if (tempAudio.duration) {
        setDuration(Math.round(tempAudio.duration));
      }
    };
  };

  const onFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      handleFileUpload(e.target.files[0]);
    }
  };

  const onDragOver = (e: React.DragEvent) => {
    e.preventDefault();
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      handleFileUpload(e.dataTransfer.files[0]);
    }
  };

  // Profiles list
  const [profiles, setProfiles] = useState<VoiceProfile[]>([]);

  // Canvas for waveform
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const analyzerRef = useRef<AnalyserNode | null>(null);
  const animFrameRef = useRef<number>();

  const loadProfiles = useCallback(async () => {
    try {
      const list = await voiceApi.listProfiles();
      setProfiles(list);
    } catch { /* backend may be offline */ }
  }, []);

  useEffect(() => { loadProfiles(); }, [loadProfiles]);

  // ── Recording ──

  const startRecording = async () => {
    try {
      setError('');
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
      chunks.current = [];

      // Waveform visualization
      const audioCtx = new AudioContext();
      const source = audioCtx.createMediaStreamSource(stream);
      const analyzer = audioCtx.createAnalyser();
      analyzer.fftSize = 256;
      source.connect(analyzer);
      analyzerRef.current = analyzer;
      drawWaveform();

      recorder.ondataavailable = (e) => { if (e.data.size > 0) chunks.current.push(e.data); };
      recorder.onstop = () => {
        const blob = new Blob(chunks.current, { type: 'audio/webm' });
        setAudioBlob(blob);
        setAudioUrl(URL.createObjectURL(blob));
        stream.getTracks().forEach(t => t.stop());
        if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current);
      };

      recorder.start();
      mediaRecorder.current = recorder;
      setIsRecording(true);
      setDuration(0);
      setAudioBlob(null);
      setAudioUrl(null);
      setSaveSuccess(false);

      timerRef.current = setInterval(() => setDuration(d => d + 1), 1000);
    } catch (err) {
      setError('Microphone access denied. Please allow microphone access.');
    }
  };

  const stopRecording = () => {
    mediaRecorder.current?.stop();
    setIsRecording(false);
    if (timerRef.current) clearInterval(timerRef.current);
  };

  const drawWaveform = () => {
    const canvas = canvasRef.current;
    const analyzer = analyzerRef.current;
    if (!canvas || !analyzer) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const bufferLength = analyzer.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);

    const draw = () => {
      animFrameRef.current = requestAnimationFrame(draw);
      analyzer.getByteTimeDomainData(dataArray);

      ctx.fillStyle = 'rgba(10, 14, 26, 0.3)';
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      ctx.lineWidth = 2;
      ctx.strokeStyle = '#8b5cf6';
      ctx.beginPath();

      const sliceWidth = canvas.width / bufferLength;
      let x = 0;

      for (let i = 0; i < bufferLength; i++) {
        const v = (dataArray[i] ?? 128) / 128.0;
        const y = (v * canvas.height) / 2;

        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
        x += sliceWidth;
      }

      ctx.lineTo(canvas.width, canvas.height / 2);
      ctx.stroke();
    };

    draw();
  };

  // ── Save Profile ──

  const saveProfile = async () => {
    if (!audioBlob || !profileName.trim()) {
      setError('Please record audio and enter a profile name.');
      return;
    }

    setSaving(true);
    setError('');
    try {
      await voiceApi.saveRecording(audioBlob, profileName, transcript || undefined, language);
      setSaveSuccess(true);
      setProfileName('');
      setTranscript('');
      setAudioBlob(null);
      setAudioUrl(null);
      setDuration(0);
      await loadProfiles();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save profile');
    } finally {
      setSaving(false);
    }
  };

  const formatTime = (sec: number) => `${Math.floor(sec / 60).toString().padStart(2, '0')}:${(sec % 60).toString().padStart(2, '0')}`;

  const deleteProfile = async (id: number) => {
    try {
      await voiceApi.deleteProfile(id);
      await loadProfiles();
    } catch { /* ignore */ }
  };

  return (
    <div className="record-page animate-fade-in">
      <h1>Record & Upload Voice</h1>
      <p className="page-subtitle">Record 15-30 seconds of clean audio or upload an existing audio file to create a voice profile</p>

      {/* Mode Selector */}
      <div className="mode-selector">
        <button
          className={`mode-tab ${activeMode === 'record' ? 'active' : ''}`}
          onClick={() => setActiveMode('record')}
        >
          🎙️ Record Audio
        </button>
        <button
          className={`mode-tab ${activeMode === 'upload' ? 'active' : ''}`}
          onClick={() => setActiveMode('upload')}
        >
          📁 Upload Sound File
        </button>
      </div>

      {activeMode === 'record' ? (
        /* Waveform & Recorder */
        <div className="card waveform-card">
          <canvas ref={canvasRef} className="waveform-canvas" width={800} height={120} />

          <div className="record-controls">
            <span className="record-timer">{formatTime(duration)}</span>

            {!isRecording ? (
              <button className="btn btn-primary btn-lg record-btn" onClick={startRecording}>
                ⏺ Start Recording
              </button>
            ) : (
              <button className="btn btn-danger btn-lg record-btn animate-recording" onClick={stopRecording}>
                ⏹ Stop Recording
              </button>
            )}

            <span className="record-hint">
              {isRecording ? 'Recording...' : duration > 0 ? `${formatTime(duration)} recorded` : 'Click to start'}
            </span>
          </div>

          {audioUrl && (
            <div className="record-preview">
              <audio controls src={audioUrl} className="record-audio" />
            </div>
          )}
        </div>
      ) : (
        /* File Upload Dropzone */
        <div
          className="card upload-dropzone"
          onDragOver={onDragOver}
          onDrop={onDrop}
          onClick={() => fileInputRef.current?.click()}
        >
          <input
            type="file"
            ref={fileInputRef}
            onChange={onFileChange}
            accept="audio/*,.wav,.mp3,.m4a,.ogg,.webm"
            style={{ display: 'none' }}
          />
          <div className="upload-icon">🎵</div>
          <div className="upload-title">Drop your audio file here or click to browse</div>
          <div className="upload-subtitle">Supports WAV, MP3, M4A, OGG, WEBM (15-30 sec recommended)</div>

          {audioUrl && (
            <div className="record-preview" onClick={e => e.stopPropagation()}>
              <audio controls src={audioUrl} className="record-audio" />
            </div>
          )}
        </div>
      )}

      {/* Save Form */}
      {audioBlob && (
        <div className="card save-form animate-slide-up">
          <h3>Save as Voice Profile</h3>

          <div className="form-row">
            <label className="form-label">Profile Name *</label>
            <input
              type="text"
              value={profileName}
              onChange={e => setProfileName(e.target.value)}
              placeholder="e.g., My Voice - Casual English"
            />
          </div>

          <div className="form-row">
            <label className="form-label">Transcript (what you said)</label>
            <textarea
              value={transcript}
              onChange={e => setTranscript(e.target.value)}
              placeholder="Type what you said in the recording..."
            />
          </div>

          <div className="form-row">
            <label className="form-label">Language</label>
            <select value={language} onChange={e => setLanguage(e.target.value)}>
              <option value="en">English</option>
              <option value="ur">Urdu (اردو)</option>
              <option value="hi">Hindi (हिन्दी)</option>
            </select>
          </div>

          {error && <div className="form-error">{error}</div>}
          {saveSuccess && <div className="form-success">✅ Profile saved successfully!</div>}

          <button
            className="btn btn-primary btn-lg"
            onClick={saveProfile}
            disabled={saving || !profileName.trim()}
          >
            {saving ? '💾 Saving...' : '💾 Save Profile'}
          </button>
        </div>
      )}

      {/* Existing Profiles */}
      {profiles.length > 0 && (
        <div className="profiles-section">
          <h2>Your Voice Profiles</h2>
          <div className="profiles-grid">
            {profiles.map(p => (
              <div key={p.id} className="card profile-card stagger-item">
                <div className="profile-header">
                  <span className="profile-name">{p.name}</span>
                  <span className="badge badge-accent">{p.language.toUpperCase()}</span>
                </div>
                {p.duration_sec && (
                  <span className="profile-duration">{formatTime(Math.round(p.duration_sec))}</span>
                )}
                <audio controls src={voiceApi.getAudioUrl(p.id)} className="profile-audio" />
                <div className="profile-actions">
                  <button className="btn btn-ghost btn-danger" onClick={() => deleteProfile(p.id)}>
                    🗑️ Delete
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
