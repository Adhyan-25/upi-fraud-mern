/**
 * index.js - process entry point:  `npm start` / `npm run dev`
 *
 * Wires real dependencies together (config, MongoDB, detection client), starts the HTTP
 * server, and shuts everything down cleanly on SIGINT / SIGTERM (Ctrl-C, `docker stop`).
 */
const config = require('./config');
const db = require('./db');
const { createApp } = require('./app');
const { createDetectionClient } = require('./services/detectionClient');

async function main() {
  await db.connect(config.mongoUri);
  console.log(`[db] connected to ${config.mongoUri.replace(/\/\/.*@/, '//***@')}`); // hide credentials if any

  const detection = createDetectionClient(config);
  const app = createApp({ config, detection });
  const server = app.listen(config.port, () => console.log(`[api] listening on http://localhost:${config.port}`));

  // Graceful shutdown: stop accepting new requests, then close MongoDB
  const shutdown = (signal) => {
    console.log(`[api] ${signal} received, shutting down`);
    server.close(async () => {
      await db.disconnect();
      process.exit(0);
    });
    setTimeout(() => process.exit(1), 10_000).unref(); // safety net if something hangs
  };
  process.on('SIGINT', () => shutdown('SIGINT'));
  process.on('SIGTERM', () => shutdown('SIGTERM'));
}

main().catch((err) => {
  console.error('[fatal] could not start:', err.message);
  process.exit(1);
});
