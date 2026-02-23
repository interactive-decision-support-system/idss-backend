import sys
import json
sys.path.append('/Users/tsarda/projects/idss-backend')
from idss.data.vehicle_store import SupabaseVehicleStore
from idss.diversification.entropy import get_vehicle_value

store = SupabaseVehicleStore()
vehicles = store.search_listings({"price": "0-35000", "body_style": "SUV"}, limit=3)
print(json.dumps(vehicles[0], indent=2))
print("EXTRACTED PRICE:", get_vehicle_value(vehicles[0], 'price'))
