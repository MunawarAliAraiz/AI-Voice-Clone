import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
// Design tokens must land before App.css, which consumes them.
import './styles/variables.css';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // A stale-while-revalidate default suits every list here (voices,
      // history, jobs) — the mutations below invalidate on completion, so
      // there's no benefit to refetching on every window focus.
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>,
);
