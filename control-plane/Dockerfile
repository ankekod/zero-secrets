FROM python:3.12-slim

# `git` is used by /v1/repo/tarball: the control plane does a real server-
# side clone (with the PAT injected via http.extraHeader so it never lands
# in .git/config) and tars up the working tree — including .git/ — to stream
# back to the sandbox. That's how the sandbox gets a clone with full upstream
# history without ever holding the PAT itself.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
