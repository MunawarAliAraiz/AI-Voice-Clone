import { NavLink } from 'react-router-dom';
import './Sidebar.css';

const NAV_ITEMS = [
  { path: '/',         icon: '🏠', label: 'Dashboard'  },
  { path: '/record',   icon: '🎙️', label: 'Record'     },
  { path: '/generate', icon: '🔊', label: 'Generate'   },
  { path: '/history',  icon: '📜', label: 'History'    },
  { path: '/settings', icon: '⚙️', label: 'Settings'   },
];

interface SidebarProps {
  backendConnected: boolean;
}

export default function Sidebar({ backendConnected }: SidebarProps) {
  return (
    <aside className="sidebar">
      {/* Logo */}
      <div className="sidebar-logo">
        <div className="sidebar-logo-icon">🎤</div>
        <div className="sidebar-logo-text">
          <span className="sidebar-logo-title">Voice Clone</span>
          <span className="sidebar-logo-subtitle">Studio</span>
        </div>
      </div>

      {/* Navigation */}
      <nav className="sidebar-nav">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            end={item.path === '/'}
            className={({ isActive }) =>
              `sidebar-nav-item ${isActive ? 'active' : ''}`
            }
          >
            <span className="sidebar-nav-icon">{item.icon}</span>
            <span className="sidebar-nav-label">{item.label}</span>
          </NavLink>
        ))}
      </nav>

      {/* Status */}
      <div className="sidebar-footer">
        <div className={`sidebar-status ${backendConnected ? 'connected' : 'disconnected'}`}>
          <span className="sidebar-status-dot" />
          <span className="sidebar-status-text">
            {backendConnected ? 'Backend Online' : 'Backend Offline'}
          </span>
        </div>
      </div>
    </aside>
  );
}
