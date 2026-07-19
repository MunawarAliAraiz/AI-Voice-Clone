import type { ReactNode } from 'react';
import type { SystemStatus } from '../../types';
import Sidebar from './Sidebar';
import './Layout.css';

interface LayoutProps {
  children: ReactNode;
  backendConnected: boolean;
  status: SystemStatus | null;
}

export default function Layout({ children, backendConnected }: LayoutProps) {
  return (
    <div className="layout">
      <Sidebar backendConnected={backendConnected} />
      <main className="layout-main">
        <div className="layout-content">
          {children}
        </div>
      </main>
    </div>
  );
}
