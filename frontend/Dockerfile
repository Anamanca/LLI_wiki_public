# ==========================================
# Bước 1: Cài đặt dependencies
# ==========================================
FROM node:22-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# ==========================================
# Bước 2: Build source code
# ==========================================
FROM node:22-slim AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

ENV NEXT_TELEMETRY_DISABLED=1
ENV NODE_OPTIONS=--max-old-space-size=4096

# Bắt buộc phải có output: 'standalone' trong next.config.js
RUN npm run build

# ==========================================
# Bước 3: Chạy Production (Chỉ copy những gì cần thiết)
# ==========================================
FROM node:22-slim AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

# Tạo user không có quyền root để bảo mật
RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs

# Copy thư mục public (chứa hình ảnh, favicon...)
COPY --from=builder /app/public ./public

# Tự động tạo thư mục cache và cấp quyền
RUN mkdir .next && chown nextjs:nodejs .next

# Copy các file cần thiết từ thư mục standalone
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

CMD ["node", "server.js"]
