"""Offline guard for every test process: no provider credentials reach Pi.

The Pi SDK lists any built-in provider whose credentials appear in the
environment. Tests assign only the loopback ``fixture/*`` models, and this
scrub makes a real provider unreachable even by mistake.
"""
import os
import re

_PROVIDER_ENV = re.compile(r"^(AWS|ANTHROPIC|OPENAI|AZURE|GOOGLE|GEMINI|VERTEX|BEDROCK|MISTRAL|GROQ|XAI|"
                           r"OPENROUTER|DEEPSEEK|CEREBRAS|HF|HUGGINGFACE|OLLAMA|COPILOT|GITHUB_TOKEN|PI_WEB_TOKEN)")
for _name in list(os.environ):
    if _PROVIDER_ENV.match(_name):
        del os.environ[_name]
os.environ["PI_OFFLINE"] = "1"
