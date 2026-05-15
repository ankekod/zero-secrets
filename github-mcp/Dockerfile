# Sidecar that runs GitHub's official `github-mcp-server` in HTTP/Streamable
# HTTP mode. The binary's native `http` subcommand serves MCP at `/mcp`, so
# we don't need a separate stdio→SSE bridge.
#
# The control plane proxies requests to this container (over the cp-internal
# network); the GITHUB_PERSONAL_ACCESS_TOKEN never leaves this container.
FROM debian:bookworm-slim

ARG GITHUB_MCP_VERSION=v1.0.3

# Pick the right binary for the build host's CPU. Running the x86_64 binary
# under ARM emulation panics with `taggedPointerPack invalid packing` — the
# Go runtime's tagged-pointer scheme assumes high pointer bits are unused,
# which doesn't hold under qemu-on-aarch64.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && ARCH=$(uname -m) \
    && case "$ARCH" in \
        x86_64)  GH_ARCH=x86_64 ;; \
        aarch64) GH_ARCH=arm64 ;; \
        *) echo "Unsupported architecture: $ARCH"; exit 1 ;; \
       esac \
    && curl -fsSL "https://github.com/github/github-mcp-server/releases/download/${GITHUB_MCP_VERSION}/github-mcp-server_Linux_${GH_ARCH}.tar.gz" \
       | tar -xz -C /usr/local/bin github-mcp-server \
    && chmod +x /usr/local/bin/github-mcp-server \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

EXPOSE 8090
ENTRYPOINT ["github-mcp-server", "http", "--port=8090"]
