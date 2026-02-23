import sys, json
sys.path.append('/Users/tsarda/projects/idss-backend')
from idss.data.vehicle_store import SupabaseVehicleStore
from idss.diversification.bucketing import bucket_vehicles_numerical
from idss.diversification.entropy import get_vehicle_value

store = SupabaseVehicleStore()
vehicles = store.search_listings({'price': '0-35000', 'body_style': 'SUV'}, limit=3)

# Emulate what bucketing does
values_with_idx = []
for i, v in enumerate(vehicles):
    val = get_vehicle_value(v, 'price')
    print(f"Index {i}, val={val}, type={type(val)}")
    if val is not None:
        values_with_idx.append((float(val), i))

print("Filtered Values:", values_with_idx)

buckets, labels = bucket_vehicles_numerical(vehicles, "price")
print("Labels:", labels)
