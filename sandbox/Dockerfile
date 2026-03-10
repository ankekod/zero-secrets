FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy agent code
COPY . .

# Create the sandbox user and workspace directory
RUN useradd -m -s /bin/bash sandbox && \
    mkdir -p /workspace && \
    chown sandbox:sandbox /workspace

# Make entrypoint executable
RUN chmod +x entrypoint.sh

# The workspace is where the agent reads/writes files
VOLUME /workspace

ENTRYPOINT ["./entrypoint.sh"]
