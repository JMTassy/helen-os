"""HELEN OS - Simplified Flask Application for Railway"""

import os
from flask import Flask, jsonify
from datetime import datetime

app = Flask(__name__)

@app.route('/')
def index():
    return jsonify({
        "name": "HELEN OS",
        "version": "1.0.0",
        "description": "Multi-Model AI Companion",
        "status": "running",
        "endpoints": {
            "GET /": "This info",
            "GET /health": "Health check",
            "GET /status": "System status",
        }
    })

@app.route('/health')
def health():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "helen_initialized": True
    })

@app.route('/status')
def status():
    return jsonify({
        "status": "online",
        "port": os.environ.get('PORT', 8000),
        "environment": os.environ.get('RAILWAY_ENVIRONMENT', 'local')
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port)
