# 用户端前端 Dockerfile - 使用国内镜像加速
FROM registry.cn-hangzhou.aliyuncs.com/library/node:18-alpine AS builder

WORKDIR /app

# 替换 Alpine apk 源为阿里云镜像
RUN sed -i 's/dl-cdn.alpinelinux.org/mirrors.aliyun.com/g' /etc/apk/repositories

# 设置 npm 淘宝镜像
RUN npm config set registry https://registry.npmmirror.com

# 构建参数
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

# 安装依赖（使用 npm ci 确保一致性）
COPY package*.json ./
RUN npm ci

# 构建应用
COPY . .
RUN npm run build

# 生产镜像
FROM registry.cn-hangzhou.aliyuncs.com/library/nginx:alpine

# 替换 Alpine apk 源为阿里云镜像
RUN sed -i 's/dl-cdn.alpinelinux.org/mirrors.aliyun.com/g' /etc/apk/repositories

# 复制构建产物
COPY --from=builder /app/dist /usr/share/nginx/html

# 复制 Nginx 配置
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
