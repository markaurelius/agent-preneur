FROM node:20-slim

# Build tools needed by some Baileys transitive deps (libsignal, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    python3 \
    make \
    g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY package*.json ./

# Baileys pulls libsignal-node from GitHub via SSH — redirect to HTTPS so no keys needed
RUN git config --global url."https://github.com/".insteadOf "ssh://git@github.com/" \
    && git config --global url."https://github.com/".insteadOf "git@github.com:"

RUN npm install --omit=dev --legacy-peer-deps

COPY index.js .

# Auth credentials are stored here — mount a named volume so they survive restarts
RUN mkdir -p /app/auth

CMD ["node", "index.js"]
