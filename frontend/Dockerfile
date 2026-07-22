# ---------------------------------------------------------------
# AI Voice Clone Studio — Frontend Dockerfile
# ---------------------------------------------------------------

# Build stage
FROM node:20-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .
RUN npm run build

# Serve stage
FROM nginx:alpine

COPY --from=builder /app/dist /usr/share/nginx/html

# Nginx config for SPA routing on port 1420
RUN echo 'server { \
    listen 1420; \
    server_name _; \
    location / { \
        root /usr/share/nginx/html; \
        index index.html index.htm; \
        try_files $uri $uri/ /index.html; \
    } \
}' > /etc/nginx/conf.d/default.conf

EXPOSE 1420

CMD ["nginx", "-g", "daemon off;"]
