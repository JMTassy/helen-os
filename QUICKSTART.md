# HELEN OS - Quick Start Guide

## Setup

### 1. Configure API Keys

Copy `.env.template` to `.env`:
```bash
cp .env.template .env
```

Edit `.env` and add your API keys:
```
GOOGLE_API_KEY=your-key-here
ANTHROPIC_API_KEY=your-key-here
OPENAI_API_KEY=your-key-here
```

### 2. Local Testing

#### Option A: Direct Python (Requires Python 3.10+)
```bash
pip install -r requirements.txt
python -m helen_os
```

#### Option B: Docker (Recommended)
```bash
docker build -t helen-os .
docker run -p 8000:8000 --env-file .env helen-os
```

## Testing

### Health Check
```bash
curl http://localhost:8000/health
```

### List Models
```bash
curl http://localhost:8000/models
```

### Send a Query
```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Hello HELEN!", "task_type": "conversation"}'
```

## Endpoints

- `GET /` - API Info
- `GET /health` - Health Check
- `GET /status` - System Status
- `GET /models` - List Available Models
- `GET /routing-info` - Routing Configuration
- `POST /query` - Process a Query

## Google Cloud Deployment

See GCP_DEPLOYMENT_PRODUCTION_GRADE.md for full deployment guide.

Quick deploy:
```bash
gcloud run deploy helen-os \
  --source . \
  --platform managed \
  --region europe-west1 \
  --set-env-vars GOOGLE_API_KEY=your-key,ANTHROPIC_API_KEY=your-key \
  --allow-unauthenticated \
  --memory 2Gi
```

## Architecture

- **Config**: Environment variable management
- **Router**: Intelligent model selection
- **API Server**: Flask REST API
- **Providers**: Claude, GPT, Grok, Gemini, Qwen

## Support

Check the terminal output for configuration status.

🧠 HELEN OS v1.0.0
