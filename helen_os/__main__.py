"""Entry point for HELEN OS"""

import sys
from .api_server import create_app
from .config import Config

def main():
    """Start HELEN OS server"""
    config = Config()

    print("\n" + "=" * 60)
    print("🧠 HELEN OS - Multi-Model AI Companion")
    print("=" * 60)
    print(f"Starting server on port {config.port}...")
    print("\nConfiguration Status:")
    for provider, available in config.available_providers.items():
        status = "✅ Available" if available else "❌ Not configured"
        print(f"  {provider.upper():12} {status}")
    print("=" * 60 + "\n")

    app = create_app()

    try:
        app.run(
            host="0.0.0.0",
            port=config.port,
            debug=config.debug,
            use_reloader=False
        )
    except KeyboardInterrupt:
        print("\n\n👋 HELEN OS shutting down gracefully...")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
