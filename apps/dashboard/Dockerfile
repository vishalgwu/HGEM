FROM node:20-alpine AS deps
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@9.12.0 --activate
COPY pnpm-workspace.yaml package.json* pnpm-lock.yaml* ./
COPY apps/dashboard/package.json          apps/dashboard/package.json
COPY packages/guardmem-sdk-ts/package.json packages/guardmem-sdk-ts/package.json
RUN pnpm install --frozen-lockfile || pnpm install

FROM node:20-alpine AS build
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@9.12.0 --activate
COPY --from=deps /app /app
COPY apps/dashboard          apps/dashboard
COPY packages/guardmem-sdk-ts packages/guardmem-sdk-ts
RUN pnpm --filter @guardmem/dashboard build

FROM node:20-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/apps/dashboard/.next    apps/dashboard/.next
COPY --from=build /app/apps/dashboard/package.json apps/dashboard/package.json
COPY --from=build /app/node_modules            node_modules
EXPOSE 3000
CMD ["node", "apps/dashboard/.next/standalone/server.js"]
