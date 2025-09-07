# Build stage
FROM node:22.19.0-slim AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y \
    python3 \
    make \
    g++ \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

#Enable pnpm
ENV PNPM_HOME="/pnpm"
ENV PATH="$PNPM_HOME:$PATH"
RUN corepack enable

WORKDIR /app

# Copy package files
COPY ./package.json /app/
COPY ./pnpm-lock.yaml /app/

# Install dependencies
RUN pnpm install --shamefully-hoist

# # Rebuild native modules for Linux
# RUN pnpm rebuild better-sqlite3

# Copy source code
COPY . .

# Build the application
# RUN NUXT_TELEMETRY_DISABLED=1 CI=1 pnpm build
RUN pnpm build

# Production stage
FROM node:22.19.0-slim AS runner

WORKDIR /app

# Install curl for health checks
RUN apt-get update && apt-get install -y curl && apt-get clean && rm -rf /var/lib/apt/lists/*

# Define environment variables
ENV HOST=0.0.0.0 NODE_ENV=production
ENV NODE_ENV=production

# Create non-root user
RUN groupadd --gid 1001 nuxtjs && useradd --uid 1001 --gid nuxtjs --shell /bin/bash --create-home nuxtjs

# Copy built application
COPY --from=builder /app/.output ./

# Switch to non-root user
USER nuxtjs

# Expose port
EXPOSE 3000

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=60s --retries=3 \
  CMD curl -f http://localhost:3000/health || exit 1

# Start the application
CMD ["node", "/app/server/index.mjs"]