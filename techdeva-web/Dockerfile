FROM node:20-alpine AS builder
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
ARG NEXT_PUBLIC_API_BASE
ENV NEXT_PUBLIC_API_BASE=${NEXT_PUBLIC_API_BASE}
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
EXPOSE 3000
CMD ["node", "server.js"]
