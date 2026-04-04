"""Configuration Manager for HELEN OS"""

import os
from dotenv import load_dotenv
from typing import Dict, Optional

load_dotenv()

class Config:
    """HELEN OS Configuration"""

    def __init__(self):
        self.port = int(os.getenv("PORT", 8000))
        self.debug = os.getenv("DEBUG", "False").lower() == "true"

        # API Keys
        self.api_keys = {
            "google": os.getenv("GOOGLE_API_KEY", ""),
            "anthropic": os.getenv("ANTHROPIC_API_KEY", ""),
            "openai": os.getenv("OPENAI_API_KEY", ""),
            "xai": os.getenv("XAI_API_KEY", ""),
            "qwen": os.getenv("QWEN_API_KEY", ""),
        }

        # Available providers
        self.available_providers = self._check_available_providers()

    def _check_available_providers(self) -> Dict[str, bool]:
        """Check which providers have valid API keys"""
        return {
            provider: bool(key and key != "")
            for provider, key in self.api_keys.items()
        }

    def get_status(self) -> Dict:
        """Get configuration status"""
        return {
            "port": self.port,
            "debug": self.debug,
            "providers": {
                provider: "✅ Configured" if available else "❌ Missing"
                for provider, available in self.available_providers.items()
            },
            "available_providers_count": sum(self.available_providers.values())
        }

    def get_api_key(self, provider: str) -> Optional[str]:
        """Get API key for a provider"""
        return self.api_keys.get(provider.lower(), "")

    def is_provider_available(self, provider: str) -> bool:
        """Check if a provider is available"""
        return self.available_providers.get(provider.lower(), False)
