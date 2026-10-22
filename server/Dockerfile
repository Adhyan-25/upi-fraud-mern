# ---- API image (Node + Express) ----
FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production
# install only production dependencies first (better Docker layer caching)
COPY package*.json ./
RUN npm install --omit=dev --no-audit --no-fund
COPY src ./src
EXPOSE 5000
# run as the unprivileged "node" user that ships with the image (never run services as root)
USER node
CMD ["node", "src/index.js"]
