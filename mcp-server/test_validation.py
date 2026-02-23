import sys, json
sys.path.append('/Users/tsarda/projects/idss-backend')
from idss.data.vehicle_store import SupabaseVehicleStore
from app.formatters import format_product
from pydantic import ValidationError

store = SupabaseVehicleStore()
vehicles = store.search_listings({'price': '0-35000', 'body_style': 'SUV'}, limit=3)
try:
    for v in vehicles:
        formatted = format_product(v, "vehicles")
        print(f"Validated {formatted.id} RetailListing OK")
except ValidationError as e:
    print(e)
