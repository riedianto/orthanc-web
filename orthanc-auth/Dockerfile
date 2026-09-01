FROM node:20-alpine

WORKDIR /app

COPY package.json ./
RUN npm install --omit=dev

COPY server.js ./
COPY views ./views

ENV PROXY_PORT=8090
ENV UPSTREAM=http://orthanc:8090

EXPOSE 8090

CMD ["node", "server.js"]
