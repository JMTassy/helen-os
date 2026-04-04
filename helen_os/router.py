"""Intelligent Model Router for HELEN OS"""

from typing import Dict, List, Tuple
from enum import Enum

class TaskType(Enum):
    """Task types for intelligent routing"""
    REASONING = "reasoning"
    CODING = "coding"
    MATH = "math"
    ANALYSIS = "analysis"
    CREATIVE = "creative"
    CONVERSATION = "conversation"
    RESEARCH = "research"

class ModelRouter:
    """Routes queries to the best available model"""

    TASK_PREFERENCES = {
        TaskType.REASONING: ["anthropic", "openai", "qwen"],
        TaskType.CODING: ["openai", "anthropic", "qwen"],
        TaskType.MATH: ["openai", "anthropic"],
        TaskType.ANALYSIS: ["anthropic", "openai", "qwen"],
        TaskType.CREATIVE: ["openai", "anthropic"],
        TaskType.CONVERSATION: ["anthropic", "openai", "qwen"],
        TaskType.RESEARCH: ["qwen", "openai", "anthropic"],
    }

    MODELS = {
        "anthropic": {
            "name": "Claude 3.5 Sonnet",
            "endpoint": "https://api.anthropic.com/v1/messages",
            "capability_score": 95,
            "cost_per_1k": 0.003,
        },
        "openai": {
            "name": "GPT-4 Turbo",
            "endpoint": "https://api.openai.com/v1/chat/completions",
            "capability_score": 92,
            "cost_per_1k": 0.01,
        },
        "google": {
            "name": "Gemini 1.5 Pro",
            "endpoint": "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent",
            "capability_score": 90,
            "cost_per_1k": 0.0015,
        },
        "xai": {
            "name": "Grok 3",
            "endpoint": "https://api.x.ai/v1/chat/completions",
            "capability_score": 85,
            "cost_per_1k": 0.002,
        },
        "qwen": {
            "name": "Qwen 2.5 72B",
            "endpoint": "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation",
            "capability_score": 88,
            "cost_per_1k": 0.0008,
        },
    }

    def __init__(self, available_providers: Dict[str, bool]):
        self.available_providers = available_providers

    def select_model(self, task_type: str, query: str = "") -> Tuple[str, Dict]:
        """
        Intelligently select the best model for a task.

        Returns:
            Tuple of (provider_name, model_config)
        """
        try:
            task = TaskType(task_type.lower())
        except ValueError:
            task = TaskType.CONVERSATION

        # Get preferred providers for this task
        preferred = self.TASK_PREFERENCES.get(task, [])

        # Find first available preferred provider
        for provider in preferred:
            if self.available_providers.get(provider, False):
                model_config = self.MODELS.get(provider, {})
                return provider, model_config

        # Fallback to first available provider
        for provider, is_available in self.available_providers.items():
            if is_available:
                model_config = self.MODELS.get(provider, {})
                return provider, model_config

        # No providers available
        return None, {}

    def list_available_models(self) -> List[Dict]:
        """List all available models"""
        available = []
        for provider, is_available in self.available_providers.items():
            if is_available and provider in self.MODELS:
                model = self.MODELS[provider].copy()
                model["provider"] = provider
                available.append(model)
        return available

    def get_routing_info(self) -> Dict:
        """Get routing configuration and available models"""
        return {
            "available_providers": {
                p: "✅" for p, a in self.available_providers.items() if a
            },
            "task_preferences": {
                task.value: prefs
                for task, prefs in self.TASK_PREFERENCES.items()
            },
            "models": self.MODELS,
        }
