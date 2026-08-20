# Saros 前端镜像：多阶段构建（node 构建 dist → nginx 托管 + 反代 /api）
# 阶段三 Java 后端替代时：前端代码与本镜像零改动，只改 nginx.conf 的 proxy_pass 目标
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
