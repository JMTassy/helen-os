# 🧠 HELEN OS - Multi-Model AI Companion

A production-ready AI companion with intelligent routing across 6 major AI providers: Claude, GPT, Grok, Gemini, and Qwen.

## Features

✅ **Intelligent Routing** - Automatically selects the best AI model for your task
✅ **Multi-Provider Support** - Claude, GPT-4, Grok, Gemini, Qwen
✅ **REST API** - Simple HTTP endpoints for integration
✅ **Docker Ready** - One-command deployment
✅ **Google Cloud Compatible** - Deploy to Cloud Run in minutes

## Quick Start

### 1. Prerequisites
- Docker installed
- Python 3.10+ (for local development)
- At least one API key (Claude, OpenAI, Google, xAI, or Alibaba)

### 2. Configuration

Copy template and add your API keys:
```bash
cp .env.template .env
# Edit .env with your API keys
```

### 3. Run Locally

**Docker (Recommended):**
```bash
docker build -t helen-os .
docker run -p 8000:8000 --env-file .env helen-os
```

**Python Direct:**
```bash
pip install -r requirements.txt
python -m helen_os
```

### 4. Test

```bash
curl http://localhost:8000/health
```

## API Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/` | API info |
| GET | `/health` | Health check |
| GET | `/status` | System status |
| GET | `/models` | List available models |
| GET | `/routing-info` | Routing config |
| POST | `/query` | Send query (auto-routing) |

## Example Requests

### Health Check
```bash
curl http://localhost:8000/health
```

### List Available Models
```bash
curl http://localhost:8000/models
```

### Send Query
```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Explain quantum computing",
    "task_type": "explanation"
  }'
```

## Deployment to Google Cloud Run

```bash
gcloud run deploy helen-os \
  --source . \
  --platform managed \
  --region europe-west1 \
  --no-invoker-iam-check \
  --set-env-vars GOOGLE_API_KEY=your-key,ANTHROPIC_API_KEY=your-key \
  --memory 2Gi \
  --cpu 2 \
  --min-instances 1
```

## Architecture

```
helen-os/
├── helen_os/
│   ├── __init__.py       # Package
│   ├── __main__.py       # Entry point
│   ├── config.py         # Configuration
│   ├── router.py         # Intelligent routing
│   └── api_server.py     # Flask API
├── Dockerfile            # Docker image
├── requirements.txt      # Python dependencies
└── .env                  # Configuration (ignored in git)
```

## Configuration

### API Keys Required

At least one of the following:
- `GOOGLE_API_KEY` - For Gemini models
- `ANTHROPIC_API_KEY` - For Claude models
- `OPENAI_API_KEY` - For GPT models
- `XAI_API_KEY` - For Grok models
- `QWEN_API_KEY` - For Qwen models

### Environment Variables

```
PORT=8000                    # Server port
DEBUG=False                  # Debug mode
```

## Task Types

Auto-routing works best with task types:
- `reasoning` - Complex logical problems
- `coding` - Code generation and debugging
- `math` - Mathematical calculations
- `analysis` - Data analysis
- `creative` - Creative writing
- `conversation` - General chat
- `research` - Research and fact-finding

## Support

For issues or questions:
1. Check endpoint response messages
2. Verify API keys are configured
3. Review Docker container logs

## License

HELEN OS v1.0.0 - 2026

---

🧠 **HELEN OS: Making AI Simple, Smart, and Accessible.**
