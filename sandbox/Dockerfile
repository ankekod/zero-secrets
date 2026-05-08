FROM python:3.12-slim

# code-server (VS Code in a browser), Node.js + npm (for opencode), opencode itself.
# All installed system-wide as root before we drop to the sandbox user at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && curl -fsSL https://code-server.dev/install.sh | sh \
    && npm install -g opencode-ai \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Non-root user that everything actually runs as. The workspace is the
# directory code-server opens and where the agent reads/writes files.
RUN useradd -m -s /bin/bash sandbox \
    && mkdir -p /workspace \
    && chown sandbox:sandbox /workspace \
    && mkdir -p /home/sandbox/.config /home/sandbox/.local /home/sandbox/.cache \
    && chown -R sandbox:sandbox /home/sandbox

WORKDIR /app

# Python deps for the file-sync background worker.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x entrypoint.sh start-services.sh

VOLUME /workspace
EXPOSE 8443

ENTRYPOINT ["./entrypoint.sh"]
