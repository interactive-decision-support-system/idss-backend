/**
 * IDSS Shopping Assistant — OpenClaw Skill v1.2
 *
 * Gives you a personal AI shopping assistant over WhatsApp, iMessage,
 * Telegram, Discord, or any chat app OpenClaw connects to.
 *
 * Features:
 *   - Find laptops, electronics, books by describing what you need
 *   - Compare products ("Dell XPS vs MacBook Pro")
 *   - Get best-value picks, pros/cons, battery life info
 *   - eBay deal search (uses /search/ebay backend + browser automation fallback)
 *
 * Setup:
 *   Option A (recommended): Tell your OpenClaw —
 *     "Install this skill from URL: https://idss.vercel.app/api/skill"
 *
 *   Option B (manual):
 *     1. Copy this file into your OpenClaw skills directory
 *     2. Set IDSS_API_URL in your OpenClaw environment
 *     3. Restart OpenClaw
 *
 * API endpoints used:
 *   POST /chat-text  — AI shopping assistant, returns pre-formatted text
 *   GET  /search/ebay — eBay listing search with price/condition filters
 */

const IDSS_API_URL = process.env.IDSS_API_URL || 'https://idss-backend.onrender.com';

// ---------------------------------------------------------------------------
// Offline-resilient text formatter (used if /chat-text is unavailable)
// ---------------------------------------------------------------------------

function shortCpu(raw) {
  return raw.replace(/^(Intel Core |AMD |Apple )/i, '').slice(0, 22);
}

function formatFallback(data) {
  const lines = [];
  if (data.message) lines.push(data.message);

  if (data.recommendations?.length) {
    const products = data.recommendations.flat();
    if (products.length) {
      lines.push('', '📦 *Top Picks:*');
      products.slice(0, 5).forEach((p, i) => {
        const price = p.price != null ? `$${Number(p.price).toLocaleString()}` : '';
        const specs = [
          p.laptop?.specs?.processor && shortCpu(p.laptop.specs.processor),
          p.laptop?.specs?.ram,
          p.laptop?.specs?.storage,
        ].filter(Boolean).join(' · ');
        lines.push(`\n${i + 1}. *${p.name || p.title}* ${price}`);
        if (specs) lines.push(`   ${specs}`);
        if (p.rating) lines.push(`   ⭐ ${Number(p.rating).toFixed(1)}`);
      });
    }
  }

  if (data.quick_replies?.length) {
    lines.push('', '💬 *You can ask:*');
    data.quick_replies.slice(0, 3).forEach(q => lines.push(`• ${q}`));
  }

  return lines.join('\n').trim();
}

// ---------------------------------------------------------------------------
// eBay URL builder (used when backend and browser both fail)
// ---------------------------------------------------------------------------

function ebaySearchUrl(query, maxPrice) {
  const base = `https://www.ebay.com/sch/i.html?_nkw=${encodeURIComponent(query)}&_sop=15`;
  return maxPrice ? base + `&_udhi=${maxPrice}` : base;
}

// ---------------------------------------------------------------------------
// Main skill export
// ---------------------------------------------------------------------------

export default {
  name: 'IDSS Shopping Assistant',
  version: '1.2.0',
  description:
    'AI shopping — find laptops, electronics, books. Compare. Personalized picks. eBay deals.',

  triggers: [
    'find me', 'looking for', 'recommend', 'suggest', 'compare',
    'which is better', ' vs ', 'versus', ' or the ',
    'laptop', 'macbook', 'thinkpad', 'gaming laptop', 'work laptop',
    'chromebook', 'notebook', 'tablet', 'phone',
    'buy', 'shop', 'deal', 'price', 'under $', 'budget',
    'ebay', 'best deal', 'cheapest',
    'worth the price', 'battery life', 'pros and cons', 'specs',
  ],

  memory: {
    idss_session_id: 'string',   // persists across messages (24h TTL)
  },

  permissions: ['network', 'memory', 'browser'],

  // ─── Main handler ────────────────────────────────────────────────────────

  async run({ message, memory, send, browse }) {
    const text = (message.text || '').trim();
    if (!text) return;

    const msgLower = text.toLowerCase();
    const isEbayRequest = /\bebay\b|best deal|cheapest listing/i.test(msgLower);

    // ── eBay fast-path ────────────────────────────────────────────────────
    if (isEbayRequest) {
      const budgetMatch = text.match(/under\s*\$?(\d+)/i);
      const maxPrice    = budgetMatch ? Number(budgetMatch[1]) : null;
      const query       = text
        .replace(/\bebay\b|best deal|cheapest listing|under\s*\$?\d+/gi, '')
        .replace(/\b(find|search|look for|get me|show me)\b/gi, '')
        .trim();

      // 1st try: our /search/ebay backend (structured JSON, no browser needed)
      try {
        const params = new URLSearchParams({ q: query });
        if (maxPrice) params.set('max_price', String(maxPrice));

        const ebayResp = await fetch(`${IDSS_API_URL}/search/ebay?${params}`);
        const ebayData = await ebayResp.json();

        if (ebayData.results?.length) {
          let reply = `🛒 *eBay: "${query}"*\n`;
          ebayData.results.forEach((item, i) => {
            reply += `\n${i + 1}. ${item.title}`;
            if (item.price)     reply += ` — ${item.price}`;
            if (item.condition) reply += ` (${item.condition})`;
            if (item.shipping)  reply += `\n   📦 ${item.shipping}`;
            reply += `\n   ${item.url}`;
          });
          await send(reply);
          return;
        }

        // source=url_only → fall through to browser
        if (ebayData.search_url && browse) {
          await send(`🔍 Browsing eBay for "${query}"...`);
          const page = await browse.open(ebayData.search_url);
          const scraped = await page.extract({
            items: [{
              title:    '.s-item__title',
              price:    '.s-item__price',
              condition:'.SECONDARY_INFO',
              url:      'a.s-item__link@href',
              shipping: '.s-item__shipping',
            }],
          });

          const items = (scraped.items || [])
            .filter(i => i.title && !i.title.includes('Shop on eBay'))
            .slice(0, 5);

          if (items.length) {
            let reply = `🛒 *eBay: "${query}"*\n`;
            items.forEach((item, i) => {
              reply += `\n${i + 1}. ${item.title}`;
              if (item.price)     reply += ` — ${item.price}`;
              if (item.condition) reply += ` (${item.condition})`;
              if (item.shipping)  reply += `\n   📦 ${item.shipping}`;
              if (item.url)       reply += `\n   ${item.url}`;
            });
            await send(reply);
          } else {
            await send(`No results found. Search yourself: ${ebayData.search_url}`);
          }
          return;
        }
      } catch (_e) {
        // all eBay paths failed — fall through to URL-only message
      }

      // Final fallback: just give the user the search URL
      await send(`Search eBay here: ${ebaySearchUrl(query, maxPrice)}`);
      return;
    }

    // ── IDSS AI shopping (main path) ──────────────────────────────────────
    let sessionId = await memory.get('idss_session_id');
    if (!sessionId) {
      sessionId = `oc-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      await memory.set('idss_session_id', sessionId, { ttl: 86400 }); // 24h
    }

    // Try /chat-text first (backend-formatted, most reliable)
    try {
      const resp = await fetch(`${IDSS_API_URL}/chat-text`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, session_id: sessionId }),
      });

      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      await send(data.text || 'No response from IDSS.');
      return;
    } catch (_chatTextErr) {
      // /chat-text unavailable — fall back to /chat + local formatter
    }

    // Fallback: /chat + format locally
    try {
      const resp = await fetch(`${IDSS_API_URL}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, session_id: sessionId }),
      });

      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      await send(formatFallback(data) || 'No response from IDSS.');
    } catch (err) {
      await send(`⚠️ Shopping assistant unavailable. (${err.message})\nTry again or visit idss.vercel.app`);
    }
  },

  // ─── Reset command ───────────────────────────────────────────────────────

  async onReset({ memory, send }) {
    await memory.delete('idss_session_id');
    await send('🔄 Shopping session cleared. What are you looking for?');
  },
};
