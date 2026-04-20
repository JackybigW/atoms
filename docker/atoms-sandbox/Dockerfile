# Base image for atoms per-project sandboxes.
# Contract:
#   - Workspace is bind-mounted at /workspace
#   - Container receives ATOMS_PROJECT_ID env var (set at `docker run -e` time)
#   - `start-dev` is on PATH and launches the project's dev server on :3000
FROM node:20-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        procps \
        python3 \
        python3-pip \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*

RUN corepack enable && corepack prepare pnpm@9.12.0 --activate

COPY start-dev /usr/local/bin/start-dev
RUN chmod +x /usr/local/bin/start-dev

WORKDIR /workspace

CMD ["sleep", "infinity"]
