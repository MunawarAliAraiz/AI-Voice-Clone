import { useState, useEffect } from 'react';
import { voiceApi, ttsApi } from '../services/api';
import type { VoiceProfile, TTSGenerateResult } from '../types';
import './GeneratePage.css';

export default function GeneratePage() {
  const [profiles, setProfiles] = useState<VoiceProfile[]>([]);
  const [selectedProfile, setSelectedProfile] = useState<number | ''>('');
  const [text, setText] = useState('');
  const [language, setLanguage] = useState('en');
  const [engine, setEngine] = useState('auto');
  const [outputFormat, setOutputFormat] = useState('wav');
  const [generating, setGenerating] = useState(false);
  const [result, setResult] = useState<TTSGenerateResult | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    voiceApi.listProfiles().then(setProfiles).catch(() => {});
  }, []);

  const handleGenerate = async () => {
    if (!selectedProfile || !text.trim()) {
      setError('Please select a voice profile and enter text.');
      return;
    }

    setGenerating(true);
    setError('');
    setResult(null);

    try {
      const res = await ttsApi.generate({
        text: text.trim(),
        profile_id: selectedProfile as number,
        language,
        engine,
        output_format: outputFormat,
      });
      setResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Generation failed');
    } finally {
      setGenerating(false);
    }
  };

  const textPlaceholders: Record<string, string> = {
    en: 'Type your text here in English...',
    ur: '...یہاں اردو میں ٹائپ کریں',
    hi: 'यहाँ हिंदी में टाइप करें...',
  };

  return (
    <div className="generate-page animate-fade-in">
      <h1>Generate Speech</h1>
      <p className="page-subtitle">Convert text to speech using your cloned voice</p>

      <div className="generate-layout">
        {/* Controls */}
        <div className="card generate-controls">
          <div className="form-row">
            <label className="form-label">Voice Profile *</label>
            <select
              value={selectedProfile}
              onChange={e => setSelectedProfile(e.target.value ? Number(e.target.value) : '')}
            >
              <option value="">Select a voice profile...</option>
              {profiles.map(p => (
                <option key={p.id} value={p.id}>{p.name} ({p.language.toUpperCase()})</option>
              ))}
            </select>
          </div>

          <div className="controls-row">
            <div className="form-row">
              <label className="form-label">Language</label>
              <select value={language} onChange={e => setLanguage(e.target.value)}>
                <option value="en">English</option>
                <option value="ur">Urdu (اردو)</option>
                <option value="hi">Hindi (हिन्दी)</option>
              </select>
            </div>

            <div className="form-row">
              <label className="form-label">Engine</label>
              <select value={engine} onChange={e => setEngine(e.target.value)}>
                <option value="auto">Auto (Recommended)</option>
                <option value="f5_tts">F5-TTS</option>
                <option value="fish_speech">Fish Speech</option>
                <option value="xtts_v2">XTTS v2</option>
                <option value="mock">Mock (Dev)</option>
              </select>
            </div>

            <div className="form-row">
              <label className="form-label">Format</label>
              <select value={outputFormat} onChange={e => setOutputFormat(e.target.value)}>
                <option value="wav">WAV</option>
                <option value="mp3">MP3</option>
              </select>
            </div>
          </div>

          <div className="form-row">
            <label className="form-label">Text to Speak *</label>
            <textarea
              value={text}
              onChange={e => setText(e.target.value)}
              placeholder={textPlaceholders[language] ?? textPlaceholders.en}
              rows={6}
              dir={language === 'ur' ? 'rtl' : 'ltr'}
              className="text-input"
            />
            <span className="char-count">{text.length} / 5000 characters</span>
          </div>

          {error && <div className="form-error">{error}</div>}

          <button
            className="btn btn-primary btn-lg generate-btn"
            onClick={handleGenerate}
            disabled={generating || !selectedProfile || !text.trim()}
          >
            {generating ? (
              <>
                <span className="animate-spin">⏳</span>
                Generating...
              </>
            ) : (
              '🔊 Generate Speech'
            )}
          </button>
        </div>

        {/* Output */}
        {result && (
          <div className="card generate-output animate-slide-up">
            <h3>✅ Generated Successfully</h3>

            <div className="output-stats">
              <div className="output-stat">
                <span className="output-stat-label">Duration</span>
                <span className="output-stat-value">{result.duration_sec?.toFixed(1)}s</span>
              </div>
              <div className="output-stat">
                <span className="output-stat-label">Speed</span>
                <span className="output-stat-value">{result.gen_time_sec.toFixed(2)}s</span>
              </div>
              <div className="output-stat">
                <span className="output-stat-label">Engine</span>
                <span className="output-stat-value">{result.engine}</span>
              </div>
            </div>

            <audio
              controls
              autoPlay
              src={ttsApi.getGeneratedAudioUrl(result.id)}
              className="output-audio"
            />

            <a
              href={ttsApi.getGeneratedAudioUrl(result.id)}
              download={`generated_${result.id}.${outputFormat}`}
              className="btn btn-secondary"
            >
              ⬇️ Download {outputFormat.toUpperCase()}
            </a>
          </div>
        )}
      </div>

      {profiles.length === 0 && (
        <div className="empty-state">
          <h3>No voice profiles yet</h3>
          <p>Go to the Record page to create your first voice profile.</p>
        </div>
      )}
    </div>
  );
}
