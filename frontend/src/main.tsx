import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
// Design tokens must land before App.css, which consumes them.
import './styles/variables.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
