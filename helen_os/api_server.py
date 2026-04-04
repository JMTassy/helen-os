"""Flask API Server for HELEN OS"""

import json
from datetime import datetime
from flask import Flask, jsonify, request
from flask_cors import CORS

from .config import Config
from .router import ModelRouter

def create_app():
    """Create and configure Flask application"""
    app = Flask(__name__)
    CORS(app)

    # Load configuration
    config = Config()
    app.config["helen_config"] = config

    # Create model router
    router = ModelRouter(config.available_providers)
    app.config["helen_router"] = router

    # Health check endpoint
    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "helen_initialized": True,
            "available_providers": sum(config.available_providers.values())
        }), 200

    # Status endpoint
    @app.route("/status", methods=["GET"])
    def status():
        return jsonify(config.get_status()), 200

    # List available models
    @app.route("/models", methods=["GET"])
    def list_models():
        return jsonify({
            "models": router.list_available_models(),
            "count": len(router.list_available_models())
        }), 200

    # Get routing information
    @app.route("/routing-info", methods=["GET"])
    def routing_info():
        return jsonify(router.get_routing_info()), 200

    # Query endpoint (simple echo for testing)
    @app.route("/query", methods=["POST"])
    def query():
        """Process a query with intelligent routing"""
        try:
            data = request.get_json()
            prompt = data.get("prompt", "")
            task_type = data.get("task_type", "conversation")

            # Select best model
            provider, model_config = router.select_model(task_type, prompt)

            if not provider:
                return jsonify({
                    "error": "No models available",
                    "message": "Please configure at least one API key"
                }), 503

            return jsonify({
                "status": "ok",
                "prompt": prompt,
                "task_type": task_type,
                "selected_model": provider,
                "model_info": model_config,
                "message": f"Query routed to {model_config.get('name', 'Unknown')} ({provider})",
                "timestamp": datetime.utcnow().isoformat()
            }), 200

        except Exception as e:
            return jsonify({
                "error": str(e),
                "message": "Failed to process query"
            }), 500

    # Info endpoint
    @app.route("/", methods=["GET"])
    def info():
        return jsonify({
            "name": "HELEN OS",
            "version": "1.0.0",
            "description": "Multi-Model AI Companion",
            "endpoints": {
                "GET /": "This info",
                "GET /health": "Health check",
                "GET /status": "System status",
                "GET /models": "List available models",
                "GET /routing-info": "Routing configuration",
                "POST /query": "Process a query with auto-routing",
            }
        }), 200

    return app
