"""
channels/ — multi-channel messaging adapters for the IDSS shopping assistant.

Each adapter receives messages from a messaging platform and bridges them to
the core `process_chat()` function in `agent/chat_endpoint.py`.

Currently supported:
  - Slack (Events API webhook)

Planned:
  - WhatsApp (Twilio / Meta Cloud API)
  - Telegram (Bot API)
  - Discord
"""
