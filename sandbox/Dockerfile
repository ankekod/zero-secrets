FROM python:3.12-slim

# code-server (VS Code in a browser), Node.js + npm (for opencode), opencode itself.
# All installed system-wide as root before we drop to the sandbox user at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates gnupg git \
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

# `git` wrapper. Real git is installed above and handles local operations
# (log/diff/status/branch/checkout/commit/merge/rebase/blame/…) normally.
# This wrapper sits earlier on PATH and intercepts the network-touching
# subcommands (push/pull/fetch/clone/ls-remote) — the sandbox has no egress
# to github.com, so those would time out. The wrapper isn't the security
# boundary (the network is); it just turns the inevitable failure into a
# clear pointer at the github MCP server.
COPY git-shim.sh /usr/local/bin/git
RUN chmod +x /usr/local/bin/git

# `sandbox-clone` — broker-mediated repo fetch. The github MCP server has
# no clone tool, so this helper calls the control plane's /v1/repo/tarball
# endpoint, pipes the gzipped archive through tar into /workspace, and runs
# `git init` so local git works on the snapshot.
COPY sandbox-clone.sh /usr/local/bin/sandbox-clone
RUN chmod +x /usr/local/bin/sandbox-clone

WORKDIR /app

# Python deps for the file-sync background worker.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x entrypoint.sh start-services.sh

VOLUME /workspace
EXPOSE 8443

ENTRYPOINT ["./entrypoint.sh"]
