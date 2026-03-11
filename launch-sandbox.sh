#!/bin/bash
# Spawns a sandbox agent container for a given task.
#
# Usage:
#   ./launch-sandbox.sh "Write a poem about clouds"
#   ./launch-sandbox.sh --resume <session-id>

set -e

CONTROL_PLANE_URL="http://control-plane:8080"
CONTROL_PLANE_HOST_URL="http://localhost:8080"
NETWORK="agent-sandbox-demo_agent-network"
IMAGE_NAME="sandbox-agent"

RESUME_SESSION=""
TASK=""
FOLLOW=true

while [[ $# -gt 0 ]]; do
    case $1 in
        --resume)
            RESUME_SESSION="$2"
            shift 2
            ;;
        --no-follow)
            FOLLOW=false
            shift
            ;;
        *)
            TASK="$1"
            shift
            ;;
    esac
done

docker build -t "$IMAGE_NAME" ./sandbox/ -q

if [ -n "$RESUME_SESSION" ]; then
    SESSION_DATA=$(curl -s "$CONTROL_PLANE_HOST_URL/sessions/$RESUME_SESSION")
    if echo "$SESSION_DATA" | grep -q "not found"; then
        echo "Session $RESUME_SESSION not found"
        exit 1
    fi

    # Token retrieval for resume is not implemented — tokens are not persisted.
    echo "Resume not supported: session token is not stored between runs."
    exit 1
else
    if [ -z "$TASK" ]; then
        echo "Usage: ./launch-sandbox.sh \"Your task here\""
        echo "       ./launch-sandbox.sh --resume <session-id>"
        exit 1
    fi

    SESSION_RESPONSE=$(curl -s -X POST "$CONTROL_PLANE_HOST_URL/sessions" \
        -H "Content-Type: application/json" \
        -d "{\"task\": \"$TASK\"}")

    SESSION_ID=$(echo "$SESSION_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['session_id'])")
    TOKEN=$(echo "$SESSION_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

    echo "Session: $SESSION_ID"
fi

# Seed the task as the first message in conversation history
curl -s -X POST "$CONTROL_PLANE_HOST_URL/messages/persist" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $TOKEN" \
    -d "{\"messages\": [{\"role\": \"system\", \"content\": \"The user's task is: $TASK\"}]}" > /dev/null

CONTAINER_ID=$(docker run -d \
    --name "sandbox-${SESSION_ID}" \
    --network "$NETWORK" \
    -e "SESSION_TOKEN=$TOKEN" \
    -e "CONTROL_PLANE_URL=$CONTROL_PLANE_URL" \
    -e "SESSION_ID=$SESSION_ID" \
    "$IMAGE_NAME")

SHORT_ID="${CONTAINER_ID:0:12}"
echo "Container: $SHORT_ID (sandbox-${SESSION_ID})"
echo ""
echo "  Logs:    docker logs -f sandbox-${SESSION_ID}"
echo "  Exec:    docker exec -it sandbox-${SESSION_ID} bash"
echo "  Session: curl localhost:8080/sessions/${SESSION_ID}"
echo "  Files:   http://localhost:9001"
echo ""

if [ "$FOLLOW" = true ]; then
    docker logs -f "sandbox-${SESSION_ID}"
fi
