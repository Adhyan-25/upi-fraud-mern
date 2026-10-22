/**
 * main.jsx - the browser entry point. Mounts <App /> into <div id="root"> from index.html.
 */
import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App.jsx';
import './styles.css';

// StrictMode runs components twice in development to surface side-effect bugs early; it does nothing in production.
createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
