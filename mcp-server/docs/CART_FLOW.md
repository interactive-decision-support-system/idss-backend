# Cart & Checkout Flow: Frontend → API → Agent → UCP → MCP → Supabase

For **signed-in users**, cart and checkout follow this path:

1. **Frontend (idss-web)**  
   - `src/services/ucp.ts`: `getCart`, `addToCart`, `removeFromCart`, `checkout`, `updateCartItem`  
   - `src/services/cart.ts`: Uses these when `userId` is set; otherwise localStorage (guest).  
   - Calls: `POST /api/action/fetch-cart`, `/api/action/add-to-cart`, `/api/action/remove-from-cart`, `/api/action/checkout`, `POST /ucp/update_cart`  
   - Base URL: `NEXT_PUBLIC_MCP_BASE_URL` or `NEXT_PUBLIC_API_BASE_URL` (MCP server, e.g. port 8001).

2. **API (MCP server)**  
   - `mcp-server/app/main.py`: Action endpoints receive `user_id` (and product_id, product_snapshot, quantity as needed).  
   - These endpoints act as the **agent** layer: they accept the request and invoke the cart/checkout logic (UCP → MCP).

3. **UCP / MCP**  
   - Same server implements the cart logic. When Supabase is configured, it uses **Supabase** (cart table + products.inventory).  
   - Implementation: `mcp-server/app/supabase_cart.py` (`get_supabase_cart_client()`, `SupabaseCartClient`).

4. **Supabase**  
   - **Cart table**: `public.cart`  
     - `id`, `user_id` (FK auth.users), `product_id`, `product_snapshot` (jsonb), `quantity`, `created_at`  
     - Unique on `(user_id, product_id)`.  
   - **Checkout**: For each cart row, decrement `products.inventory` by `quantity`; then delete all cart rows for that `user_id`.  
   - Env: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (or `SUPABASE_KEY`).

If Supabase is not configured, the action endpoints fall back to an **in-memory** cart keyed by `user_id` (no persistence across restarts).
