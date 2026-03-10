#!/bin/bash
# ============================================================
# launch-sandbox.sh — Spawn a new sandbox agent
# ============================================================
# Usage:
#   ./launch-sandbox.sh "Write a poem about clouds"
#   ./launch-sandbox.sh --resume <session-id>
#
# This script:
# 1. Calls the control plane to create a session
# 2. Builds the sandbox image (if needed)
# 3. Runs a new Docker container with ONLY 3 env vars
# 4. The container is on the same Docker network as the control plane
# ============================================================

set -e

CONTROL_PLANE_URL="http://control-plane:8080"
CONTROL_PLANE_HOST_URL="http://localhost:8080"
NETWORK="agent-sandbox-demo_agent-network"
IMAGE_NAME="sandbox-agent"

# ── Parse arguments ──
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

# ── Build sandbox image if needed ──
echo "Building sandbox image..."
docker build -t "$IMAGE_NAME" ./sandbox/ -q

if [ -n "$RESUME_SESSION" ]; then
    # ── Resume existing session ──
    echo "🔄 Resuming session: $RESUME_SESSION"

    # Fetch session to get token
    SESSION_DATA=$(curl -s "$CONTROL_PLANE_HOST_URL/sessions/$RESUME_SESSION")
    if echo "$SESSION_DATA" | grep -q "not found"; then
        echo "Session $RESUME_SESSION not found"
        exit 1
    fi

    # For resume, we'd need to store/retrieve the token.
    # In a real system this would come from a database.
    # For the demo, we create a new session pointing to the same history.
    echo "⚠️  Note: In production, the token would be retrieved from secure storage."
    echo "   For this demo, create a new session instead."
    exit 1
else
    # ── Create new session ──
    if [ -z "$TASK" ]; then
        echo "Usage: ./launch-sandbox.sh \"Your task here\""
        echo "       ./launch-sandbox.sh --resume <session-id>"
        exit 1
    fi

    echo "📋 Creating session for task: $TASK"

    SESSION_RESPONSE=$(curl -s -X POST "$CONTROL_PLANE_HOST_URL/sessions" \
        -H "Content-Type: application/json" \
        -d "{\"task\": \"$TASK\"}")

    SESSION_ID=$(echo "$SESSION_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['session_id'])")
    TOKEN=$(echo "$SESSION_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

    echo "Session created: $SESSION_ID"
fi

# ── Seed the task into conversation history ──
# The control plane stores the task, but we also send it as the first message
# so the LLM knows what to do
curl -s -X POST "$CONTROL_PLANE_HOST_URL/messages/persist" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $TOKEN" \
    -d "{\"messages\": [{\"role\": \"system\", \"content\": \"The user's task is: $TASK\"}]}" > /dev/null

# ── Launch the sandbox container ──
echo "Launching sandbox container..."
echo "Session:  $SESSION_ID"
echo "Network:  $NETWORK"
echo "Env vars: SESSION_TOKEN, CONTROL_PLANE_URL, SESSION_ID (that's it!)"
echo ""

CONTAINER_ID=$(docker run -d \
    --name "sandbox-${SESSION_ID}" \
    --network "$NETWORK" \
    -e "SESSION_TOKEN=$TOKEN" \
    -e "CONTROL_PLANE_URL=$CONTROL_PLANE_URL" \
    -e "SESSION_ID=$SESSION_ID" \
    "$IMAGE_NAME")

SHORT_ID="${CONTAINER_ID:0:12}"
echo "📦 Container: $SHORT_ID (sandbox-${SESSION_ID})"
echo ""
echo "── Useful commands ──"
echo "  Logs:     docker logs -f sandbox-${SESSION_ID}"
echo "  Exec:     docker exec -it sandbox-${SESSION_ID} bash"
echo "  Check env: docker exec sandbox-${SESSION_ID} env"
echo "  Session:  curl localhost:8080/sessions/${SESSION_ID}"
echo "  Files:    Open http://localhost:9001 (MinIO console)"
echo ""

if [ "$FOLLOW" = true ]; then
    echo "── Following sandbox logs (Ctrl+C to detach) ──"
    docker logs -f "sandbox-${SESSION_ID}"
fi
