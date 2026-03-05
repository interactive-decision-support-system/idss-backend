# IDSS Shopping Assistant — OpenClaw Skill

Connect your AI shopping assistant to WhatsApp, iMessage, Telegram, Discord, or any messaging app via [OpenClaw](https://openclaw.ai).

## What This Gives You

- Ask for product recommendations in plain English, in any chat app
- Compare products ("Dell XPS vs MacBook Pro for ML work")
- Search eBay for best deals (uses OpenClaw browser automation)
- Persistent session memory — conversation context carries across messages

---

## Prerequisites

1. **OpenClaw installed** — `curl -fsSL https://openclaw.ai/install.sh | bash`
2. **IDSS backend deployed** — either local (`http://localhost:8001`) or production URL
3. **Messaging app connected** — WhatsApp, iMessage, Telegram, etc. (follow OpenClaw setup)

---

## Installation

### Step 1 — Copy the skill

```bash
# Find your OpenClaw skills directory (usually ~/openclaw/skills/)
cp idss-shopping.js ~/openclaw/skills/idss-shopping.js
```

### Step 2 — Set your API URL

In your OpenClaw environment config (`.env` or OpenClaw settings):

```
IDSS_API_URL=https://your-idss-backend.onrender.com
# or for local dev:
IDSS_API_URL=http://localhost:8001
```

### Step 3 — Enable browser permission (for eBay search)

In OpenClaw settings, grant the `idss-shopping` skill the `browser` permission.
This allows it to search eBay and scrape live prices.

### Step 4 — Restart OpenClaw

```bash
openclaw restart
```

---

## Usage Examples

Send any of these from WhatsApp/iMessage/Telegram:

| You say | What happens |
|---|---|
| `find me a gaming laptop under $900` | AI interviews you, shows ranked picks |
| `compare MacBook Air vs Dell XPS 13` | Skips interview, compares both directly |
| `I need a laptop for ML with 32GB RAM` | Extracts specs, searches matching products |
| `ebay MacBook Pro M3` | Searches eBay live for best listings |
| `which has better battery?` | Follow-up about current recommendations |
| `reset shopping` | Starts a new session |

---

## Architecture

```
Your WhatsApp / iMessage / Telegram
         ↓
    OpenClaw (on your machine)
         ↓  triggers on shopping keywords
  idss-shopping.js skill
         ↓  POST /chat
  IDSS Backend API (FastAPI)
         ↓
  AI Agent + Product Database (Supabase)
         ↑
  Response formatted as text → OpenClaw → back to chat app
```

---

## eBay Integration

When you say `ebay [product name]`, the skill:
1. Opens eBay search in OpenClaw's browser
2. Extracts top 5 listings (title, price, condition, shipping, URL)
3. Returns formatted list to your chat app
4. Optionally cross-references with our AI-curated database

To make offers on eBay on your behalf, tell your OpenClaw:
> "I want you to watch eBay for [product] and alert me when price drops below $X"

OpenClaw's proactive heartbeat scheduler will monitor and notify you.

---

## Credential Security

- Your eBay credentials should be stored in **OpenClaw's secure memory**, not in this skill file
- Tell OpenClaw: "Remember my eBay username is X and password is Y" — it encrypts them locally
- This skill never transmits credentials to the IDSS backend

---

## Customization

Edit `idss-shopping.js` to:
- Add more trigger keywords
- Change response formatting (emoji, length, language)
- Add filters for specific product categories
- Integrate with other platforms (Amazon, Best Buy, etc.)

OpenClaw can also modify its own skills — just tell it what you want changed.
