import { useState, useEffect } from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import Layout from './components/layout/Layout';
import DashboardPage from './pages/DashboardPage';
import RecordPage from './pages/RecordPage';
import GeneratePage from './pages/GeneratePage';
import HistoryPage from './pages/HistoryPage';
import SettingsPage from './pages/SettingsPage';
import type { SystemStatus } from './types';
import { systemApi } from './services/api';
import './styles/index.css';

function App() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [backendConnected, setBackendConnected] = useState(false);

  useEffect(() => {
    const checkBackend = async () => {
      try {
        const s = await systemApi.getStatus();
        setStatus(s);
        setBackendConnected(true);
      } catch {
        setBackendConnected(false);
      }
    };

    checkBackend();
    const interval = setInterval(checkBackend, 10000);
    return () => clearInterval(interval);
  }, []);

  return (
    <BrowserRouter>
      <Layout backendConnected={backendConnected} status={status}>
        <Routes>
          <Route path="/" element={<DashboardPage status={status} />} />
          <Route path="/record" element={<RecordPage />} />
          <Route path="/generate" element={<GeneratePage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/settings" element={<SettingsPage status={status} />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  );
}

export default App;
