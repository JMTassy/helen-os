#!/bin/bash

echo "🧠 HELEN OS - Local Docker Test"
echo "================================"
echo ""

# Check if .env exists
if [ ! -f .env ]; then
    echo "⚠️  .env file not found. Creating from template..."
    cp .env.template .env
    echo "✅ .env created. Please edit it with your API keys!"
    echo ""
fi

echo "📦 Building Docker image..."
docker build -t helen-os:latest .

if [ $? -ne 0 ]; then
    echo "❌ Docker build failed"
    exit 1
fi

echo "✅ Image built successfully"
echo ""
echo "🚀 Starting container..."
docker run -p 8000:8000 --env-file .env helen-os:latest &
DOCKER_PID=$!

# Wait for server to start
echo "⏳ Waiting for server to start (5 seconds)..."
sleep 5

echo ""
echo "🧪 Testing endpoints..."
echo ""

# Test health endpoint
echo "1️⃣  Testing /health..."
curl -s http://localhost:8000/health | python -m json.tool
echo ""

# Test models endpoint
echo "2️⃣  Testing /models..."
curl -s http://localhost:8000/models | python -m json.tool
echo ""

# Test status endpoint
echo "3️⃣  Testing /status..."
curl -s http://localhost:8000/status | python -m json.tool
echo ""

# Test query endpoint
echo "4️⃣  Testing /query..."
curl -s -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Hello HELEN!", "task_type": "conversation"}' | python -m json.tool
echo ""

echo "✅ All tests completed!"
echo "🧠 Server is running on http://localhost:8000"
echo ""
echo "Press Ctrl+C to stop the server"
echo ""

wait $DOCKER_PID
