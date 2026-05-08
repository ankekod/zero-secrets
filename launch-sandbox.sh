#!/bin/bash
# Spawns a sandbox container with code-server (VS Code) + opencode.
#
# Usage:
#   ./launch-sandbox.sh                       # unlabeled session
#   ./launch-sandbox.sh "Refactor parser"     # label the session
#   ./launch-sandbox.sh --port 8444 "..."     # use a different host port
#
# Only one sandbox can use port 8443 at a time. For parallel sandboxes,
# pass --port to map each container's 8443 to a different host port.
set -e

CONTROL_PLANE_URL="http://control-plane:8080"
CONTROL_PLANE_HOST_URL="http://localhost:8080"
NETWORK="agent-sandbox-demo_agent-network"
IMAGE_NAME="sandbox-agent"
HOST_PORT=8443
TASK=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --port)
            HOST_PORT="$2"
            shift 2
            ;;
        *)
            TASK="$1"
            shift
            ;;
    esac
done

if [ -z "$TASK" ]; then
    TASK="Interactive coding session"
fi

docker build -t "$IMAGE_NAME" ./sandbox/ -q

SESSION_RESPONSE=$(curl -s -X POST "$CONTROL_PLANE_HOST_URL/sessions" \
    -H "Content-Type: application/json" \
    -d "{\"task\": \"$TASK\"}")

SESSION_ID=$(echo "$SESSION_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['session_id'])")
TOKEN=$(echo "$SESSION_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

CONTAINER_ID=$(docker run -d \
    --name "sandbox-${SESSION_ID}" \
    --network "$NETWORK" \
    -p "${HOST_PORT}:8443" \
    -e "SESSION_TOKEN=$TOKEN" \
    -e "CONTROL_PLANE_URL=$CONTROL_PLANE_URL" \
    -e "SESSION_ID=$SESSION_ID" \
    "$IMAGE_NAME")

SHORT_ID="${CONTAINER_ID:0:12}"

# Wait for code-server to start accepting connections.
printf "  Waiting for code-server"
for _ in $(seq 1 30); do
    if curl -sf --max-time 1 -o /dev/null "http://localhost:${HOST_PORT}/" 2>/dev/null; then
        printf " ready\n"
        break
    fi
    printf "."
    sleep 1
done

cat <<EOF

  Session:   $SESSION_ID
  Container: $SHORT_ID

  → Open VS Code:  http://localhost:${HOST_PORT}

  Logs:    docker logs -f sandbox-${SESSION_ID}
  Exec:    docker exec -it --user sandbox sandbox-${SESSION_ID} bash
  Audit:   curl localhost:8080/sessions/${SESSION_ID}
  Stop:    docker stop sandbox-${SESSION_ID}

EOF
