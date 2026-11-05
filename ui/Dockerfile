# Стадия сборки (Builder)
FROM node:20-alpine AS builder
WORKDIR /app

# Включаем corepack для использования pnpm
RUN corepack enable

# Копируем файлы зависимостей
COPY package.json pnpm-lock.yaml* ./

# Устанавливаем зависимости
RUN pnpm install --frozen-lockfile

# Копируем исходный код
COPY . .

ARG NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_ASSISTANT_ID=agent
ARG NEXT_PUBLIC_AUTH_SCHEME
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_ASSISTANT_ID=$NEXT_PUBLIC_ASSISTANT_ID
ENV NEXT_PUBLIC_AUTH_SCHEME=$NEXT_PUBLIC_AUTH_SCHEME

# Собираем Next.js приложение
RUN pnpm run build

# Стадия запуска (Runner)
FROM node:20-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production

# Включаем corepack и в финальном образе
RUN corepack enable

# Копируем только необходимые для работы файлы из стадии сборки
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/package.json ./package.json
COPY --from=builder /app/node_modules ./node_modules

# Создаем пустую папку public на случай, если какие-то внутренние скрипты Next.js её ожидают
RUN mkdir -p ./public

EXPOSE 3000

# Запускаем приложение
CMD ["pnpm", "start"]