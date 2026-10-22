# PM Studio 前端镜像：构建用 node，静态文件交给 Caddy 伺服。
#
# **为什么运行阶段用 Caddy 而不是 nginx**：仓库根目录已经有一份 `Caddyfile`
# （生产反代 + gzip + 静态文件），用它就少一份配置——**不新增第二份反代配置**。
#
# ⚠️ **alpine 在这里没问题**：前端没有 onnxruntime（那是后端的依赖）。

FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# `npm run build` 已经含 `tsc --noEmit`（见 package.json）——
# 类型不过就不出产物，**构建失败早于部署失败**。
RUN npm run build

FROM caddy:2-alpine
COPY --from=build /app/dist /srv
COPY ../Caddyfile /etc/caddy/Caddyfile
ENV PMSTUDIO_WEB_ROOT=/srv
EXPOSE 80
