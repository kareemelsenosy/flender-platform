# ─── Builder ──────────────────────────────────────────────────────────────────
FROM node:20-slim AS builder

WORKDIR /app

# Install dependencies (cached unless package files change)
COPY package.json package-lock.json ./
RUN npm ci

# Build the Next.js app
COPY . .
RUN npm run build

# ─── Runner ───────────────────────────────────────────────────────────────────
FROM node:20-slim AS runner

WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000

# Production dependencies only
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force

# Built output + runtime config
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/next.config.mjs ./next.config.mjs

# node:20-slim already ships a `node` user at UID 1000 — reuse it rather than
# creating a duplicate. Ownership is fixed up so npm can write caches at runtime.
RUN chown -R node:node /app
USER node

EXPOSE 3000

CMD ["npm", "start"]
