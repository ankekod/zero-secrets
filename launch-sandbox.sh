#!/bin/bash
# Spawns a sandbox container with code-server (VS Code) + opencode.
#
# Usage:
#   ./launch-sandbox.sh                       # unlabeled session
#   ./launch-sandbox.sh "Refactor parser"     # label the session
#   ./launch-sandbox.sh --port 8444 "..."     # use a different host port
#   ./launch-sandbox.sh --resume <session-id> # re-attach to a prior session
#
# Only one sandbox can use port 8443 at a time. For parallel sandboxes,
# pass --port to map each container's 8443 to a different host port.
#
# --resume re-uses the session_id and token from a previous launch, and the
# new sandbox pulls any files previously synced to MinIO back into /workspace
# at startup. The control plane keeps sessions in memory, so resume only
# works for as long as the control plane has been running.
set -e

CONTROL_PLANE_URL="http://control-plane:8080"
CONTROL_PLANE_HOST_URL="http://localhost:8080"
NETWORK="agent-sandbox-demo_agent-network"
IMAGE_NAME="sandbox-agent"
HOST_PORT=8443
TASK=""
RESUME_ID=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --port)
            HOST_PORT="$2"
            shift 2
            ;;
        --resume)
            RESUME_ID="$2"
            shift 2
            ;;
        *)
            TASK="$1"
            shift
            ;;
    esac
done

docker build -t "$IMAGE_NAME" ./sandbox/ -q

if [ -n "$RESUME_ID" ]; then
    SESSION_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST \
        "$CONTROL_PLANE_HOST_URL/sessions/$RESUME_ID/resume")
    HTTP_CODE=$(echo "$SESSION_RESPONSE" | tail -n1)
    SESSION_RESPONSE=$(echo "$SESSION_RESPONSE" | sed '$d')
    if [ "$HTTP_CODE" != "200" ]; then
        echo "Failed to resume session $RESUME_ID (HTTP $HTTP_CODE):" >&2
        echo "  $SESSION_RESPONSE" >&2
        exit 1
    fi
    RESUME_FLAG=1
else
    if [ -z "$TASK" ]; then
        TASK="Interactive coding session"
    fi
    SESSION_RESPONSE=$(curl -s -X POST "$CONTROL_PLANE_HOST_URL/sessions" \
        -H "Content-Type: application/json" \
        -d "{\"task\": \"$TASK\"}")
    RESUME_FLAG=0
fi

SESSION_ID=$(echo "$SESSION_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['session_id'])")
TOKEN=$(echo "$SESSION_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

# Resume reuses the session_id, so the container name "sandbox-<id>" collides
# with the one from the previous launch. `docker stop` leaves the stopped
# container around by name — clear it out here so the new run can take over.
# If the old container is still running we refuse, since racing two sandboxes
# on the same session (same token, same S3 prefix) is rarely what you want.
if [ "$RESUME_FLAG" = "1" ]; then
    EXISTING=$(docker ps -aq --filter "name=^sandbox-${SESSION_ID}$")
    if [ -n "$EXISTING" ]; then
        if [ -n "$(docker ps -q --filter "name=^sandbox-${SESSION_ID}$")" ]; then
            echo "Container sandbox-${SESSION_ID} is still running. Stop it first:" >&2
            echo "  docker stop sandbox-${SESSION_ID}" >&2
            exit 1
        fi
        docker rm "sandbox-${SESSION_ID}" >/dev/null
    fi
fi

CONTAINER_ID=$(docker run -d \
    --name "sandbox-${SESSION_ID}" \
    --network "$NETWORK" \
    -p "${HOST_PORT}:8443" \
    -e "SESSION_TOKEN=$TOKEN" \
    -e "CONTROL_PLANE_URL=$CONTROL_PLANE_URL" \
    -e "SESSION_ID=$SESSION_ID" \
    -e "RESUME_SESSION=$RESUME_FLAG" \
    -e "GITHUB_REPO=${GITHUB_REPO:-}" \
    -e "GITHUB_BRANCH=${GITHUB_BRANCH:-main}" \
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

if [ "$RESUME_FLAG" = "1" ]; then
    MODE="resumed"
else
    MODE="new"
fi

cat <<EOF

  Session:   $SESSION_ID ($MODE)
  Container: $SHORT_ID

  → Open VS Code:  http://localhost:${HOST_PORT}

  Logs:    docker logs -f sandbox-${SESSION_ID}
  Exec:    docker exec -it --user sandbox sandbox-${SESSION_ID} bash
  Audit:   curl localhost:8080/sessions/${SESSION_ID}
  Stop:    docker stop sandbox-${SESSION_ID}     # leaves container for log inspection
  Resume:  ./launch-sandbox.sh --resume ${SESSION_ID}  # auto-removes the stopped container

EOF
