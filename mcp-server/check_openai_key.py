#!/usr/bin/env python3
"""
Quick script to check if OPENAI_API_KEY is being loaded correctly.
Run this from the mcp-server directory to verify the .env file is found.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Try loading .env from project root
env_path = Path(__file__).parent.parent.parent / '.env'
print(f"Looking for .env at: {env_path}")
print(f".env file exists: {env_path.exists()}")

if env_path.exists():
    load_dotenv(env_path)
else:
    # Fallback: try current directory and parent directories
    print("Trying default load_dotenv()...")
    load_dotenv()

# Check if key is loaded
api_key = os.getenv("OPENAI_API_KEY")
if api_key:
    print(f"[OK] OPENAI_API_KEY is loaded!")
    print(f"   Key starts with: {api_key[:15]}...")
    print(f"   Key length: {len(api_key)} characters")
else:
    print("[FAIL] OPENAI_API_KEY is NOT loaded")
    print("   Check that .env file exists and contains OPENAI_API_KEY=...")
