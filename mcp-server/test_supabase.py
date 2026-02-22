import asyncio
from idss.data.vehicle_store import SupabaseVehicleStore

async def main():
    store = SupabaseVehicleStore()
    vehicles = store.search_listings({"body_style": "SUV"}, limit=1)
    for v in vehicles:
        print(v["vehicle"]["price"])

asyncio.run(main())
