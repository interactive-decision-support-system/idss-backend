import os
import httpx
from typing import Dict, Any, List, Optional
from idss.utils.logger import get_logger

logger = get_logger("utils.supabase_client")

class SupabaseClient:
    """
    Lightweight client for interacting with Supabase REST API.
    """
    def __init__(self):
        self.url = os.environ.get("SUPABASE_URL")
        self.key = os.environ.get("SUPABASE_KEY")
        
        if not self.url or not self.key:
            logger.warning("SUPABASE_URL or SUPABASE_KEY not set in environment.")
            
        self.headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation"
        }
        self.client = httpx.Client(base_url=self.url, headers=self.headers, timeout=30.0)

    def select(self, table: str, filters: Optional[Dict[str, str]] = None, select: str = "*", limit: Optional[int] = None, order: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Query a Supabase table.
        """
        params = {"select": select}
        if filters:
            for key, val in filters.items():
                if isinstance(val, str) and "." in val and val.split(".")[0] in ("eq", "neq", "gt", "lt", "gte", "lte", "like", "ilike", "in", "is"):
                    params[key] = val
                else:
                    params[key] = f"eq.{val}"
        
        if limit:
            params["limit"] = str(limit)
        
        if order:
            params["order"] = order

        try:
            response = self.client.get(f"/rest/v1/{table}", params=params)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Supabase select failed on {table}: {e}")
            return []

    def rpc(self, function: str, params: Dict[str, Any]) -> Any:
        """
        Call a Supabase RPC function.
        """
        try:
            response = self.client.post(f"/rest/v1/rpc/{function}", json=params)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Supabase RPC failed for {function}: {e}")
            return None

# Singleton instance
supabase = SupabaseClient()
