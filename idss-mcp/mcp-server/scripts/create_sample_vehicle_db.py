#!/usr/bin/env python3
"""
Create a minimal sample vehicle database for testing purposes.
This creates a SQLite database with the expected schema and sample vehicles.
"""

import sqlite3
import json
from pathlib import Path

# Database path - adjust based on script location
# If script is in idss-mcp/mcp-server/scripts/, go up 3 levels to reach repo root
# If script is in scripts/, go up 2 levels
SCRIPT_DIR = Path(__file__).parent
if "mcp-server" in str(SCRIPT_DIR):
    # We're in idss-mcp/mcp-server/scripts/
    DB_PATH = SCRIPT_DIR.parent.parent.parent / "data" / "car_dataset_idss" / "uni_vehicles.db"
else:
    # We're in scripts/
    DB_PATH = SCRIPT_DIR.parent / "data" / "car_dataset_idss" / "uni_vehicles.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# Sample vehicles data
SAMPLE_VEHICLES = [
    {
        "vin": "1HGBH41JXMN109186",
        "year": 2021,
        "make": "Honda",
        "model": "Accord",
        "trim": "Sport",
        "body_style": "Sedan",
        "price": 28500.00,
        "mileage": 15000,
        "fuel_type": "Gasoline",
        "drivetrain": "FWD",
        "transmission": "Automatic",
        "exterior_color": "Platinum White Pearl",
        "interior_color": "Black",
        "dealer": "Honda of San Francisco",
        "city": "San Francisco",
        "state": "CA",
        "primaryImage": "https://example.com/honda-accord.jpg",
        "vdp": "https://example.com/vehicle/1HGBH41JXMN109186"
    },
    {
        "vin": "5YJ3E1EA1KF123456",
        "year": 2023,
        "make": "Tesla",
        "model": "Model 3",
        "trim": "Long Range",
        "body_style": "Sedan",
        "price": 45990.00,
        "mileage": 5000,
        "fuel_type": "Electric",
        "drivetrain": "AWD",
        "transmission": "Automatic",
        "exterior_color": "Pearl White",
        "interior_color": "Black",
        "dealer": "Tesla Store Palo Alto",
        "city": "Palo Alto",
        "state": "CA",
        "primaryImage": "https://example.com/tesla-model3.jpg",
        "vdp": "https://example.com/vehicle/5YJ3E1EA1KF123456"
    },
    {
        "vin": "1FTFW1ET5MFC12345",
        "year": 2022,
        "make": "Ford",
        "model": "F-150",
        "trim": "XLT",
        "body_style": "Truck",
        "price": 42500.00,
        "mileage": 12000,
        "fuel_type": "Gasoline",
        "drivetrain": "4WD",
        "transmission": "Automatic",
        "exterior_color": "Oxford White",
        "interior_color": "Black",
        "dealer": "Ford of San Jose",
        "city": "San Jose",
        "state": "CA",
        "primaryImage": "https://example.com/ford-f150.jpg",
        "vdp": "https://example.com/vehicle/1FTFW1ET5MFC12345"
    },
    {
        "vin": "WBA3A5C59EK123456",
        "year": 2022,
        "make": "BMW",
        "model": "X5",
        "trim": "xDrive40i",
        "body_style": "SUV",
        "price": 58900.00,
        "mileage": 8000,
        "fuel_type": "Gasoline",
        "drivetrain": "AWD",
        "transmission": "Automatic",
        "exterior_color": "Mineral White",
        "interior_color": "Black",
        "dealer": "BMW of San Francisco",
        "city": "San Francisco",
        "state": "CA",
        "primaryImage": "https://example.com/bmw-x5.jpg",
        "vdp": "https://example.com/vehicle/WBA3A5C59EK123456"
    },
    {
        "vin": "JTMB1RFV8KD123456",
        "year": 2023,
        "make": "Toyota",
        "model": "RAV4",
        "trim": "XLE",
        "body_style": "SUV",
        "price": 32900.00,
        "mileage": 3000,
        "fuel_type": "Hybrid",
        "drivetrain": "AWD",
        "transmission": "Automatic",
        "exterior_color": "Lunar Rock",
        "interior_color": "Black",
        "dealer": "Toyota of Oakland",
        "city": "Oakland",
        "state": "CA",
        "primaryImage": "https://example.com/toyota-rav4.jpg",
        "vdp": "https://example.com/vehicle/JTMB1RFV8KD123456"
    },
    {
        "vin": "1C4HJXDN8MW123456",
        "year": 2022,
        "make": "Jeep",
        "model": "Wrangler",
        "trim": "Sahara",
        "body_style": "SUV",
        "price": 42900.00,
        "mileage": 10000,
        "fuel_type": "Gasoline",
        "drivetrain": "4WD",
        "transmission": "Automatic",
        "exterior_color": "Sting-Gray",
        "interior_color": "Black",
        "dealer": "Jeep of San Francisco",
        "city": "San Francisco",
        "state": "CA",
        "primaryImage": "https://example.com/jeep-wrangler.jpg",
        "vdp": "https://example.com/vehicle/1C4HJXDN8MW123456"
    },
    {
        "vin": "5N1AR2MM9FC123456",
        "year": 2021,
        "make": "Nissan",
        "model": "Altima",
        "trim": "SV",
        "body_style": "Sedan",
        "price": 24900.00,
        "mileage": 20000,
        "fuel_type": "Gasoline",
        "drivetrain": "FWD",
        "transmission": "Automatic",
        "exterior_color": "Super Black",
        "interior_color": "Charcoal",
        "dealer": "Nissan of San Francisco",
        "city": "San Francisco",
        "state": "CA",
        "primaryImage": "https://example.com/nissan-altima.jpg",
        "vdp": "https://example.com/vehicle/5N1AR2MM9FC123456"
    },
    {
        "vin": "1G1BE5SM9K7123456",
        "year": 2023,
        "make": "Chevrolet",
        "model": "Camaro",
        "trim": "LT1",
        "body_style": "Coupe",
        "price": 34900.00,
        "mileage": 2000,
        "fuel_type": "Gasoline",
        "drivetrain": "RWD",
        "transmission": "Manual",
        "exterior_color": "Rally Green",
        "interior_color": "Black",
        "dealer": "Chevrolet of San Francisco",
        "city": "San Francisco",
        "state": "CA",
        "primaryImage": "https://example.com/chevrolet-camaro.jpg",
        "vdp": "https://example.com/vehicle/1G1BE5SM9K7123456"
    },
    {
        "vin": "4T1B11HK5JU123456",
        "year": 2022,
        "make": "Toyota",
        "model": "Camry",
        "trim": "XSE",
        "body_style": "Sedan",
        "price": 31900.00,
        "mileage": 15000,
        "fuel_type": "Hybrid",
        "drivetrain": "FWD",
        "transmission": "Automatic",
        "exterior_color": "Midnight Black Metallic",
        "interior_color": "Red",
        "dealer": "Toyota of San Francisco",
        "city": "San Francisco",
        "state": "CA",
        "primaryImage": "https://example.com/toyota-camry.jpg",
        "vdp": "https://example.com/vehicle/4T1B11HK5JU123456"
    },
    {
        "vin": "WAUAF48H17K123456",
        "year": 2023,
        "make": "Audi",
        "model": "A4",
        "trim": "Premium Plus",
        "body_style": "Sedan",
        "price": 42900.00,
        "mileage": 4000,
        "fuel_type": "Gasoline",
        "drivetrain": "AWD",
        "transmission": "Automatic",
        "exterior_color": "Glacier White",
        "interior_color": "Black",
        "dealer": "Audi of San Francisco",
        "city": "San Francisco",
        "state": "CA",
        "primaryImage": "https://example.com/audi-a4.jpg",
        "vdp": "https://example.com/vehicle/WAUAF48H17K123456"
    },
    {
        "vin": "1FTBW3CM9HKA12345",
        "year": 2023,
        "make": "Ford",
        "model": "Transit",
        "trim": "350 XLT",
        "body_style": "Van",
        "price": 45900.00,
        "mileage": 2000,
        "fuel_type": "Gasoline",
        "drivetrain": "RWD",
        "transmission": "Automatic",
        "exterior_color": "Oxford White",
        "interior_color": "Gray",
        "dealer": "Ford Commercial San Jose",
        "city": "San Jose",
        "state": "CA",
        "primaryImage": "https://example.com/ford-transit.jpg",
        "vdp": "https://example.com/vehicle/1FTBW3CM9HKA12345"
    },
    {
        "vin": "1GCVKREC5JZ123456",
        "year": 2022,
        "make": "Mercedes-Benz",
        "model": "Sprinter",
        "trim": "2500 Crew",
        "body_style": "Van",
        "price": 54900.00,
        "mileage": 8000,
        "fuel_type": "Diesel",
        "drivetrain": "RWD",
        "transmission": "Automatic",
        "exterior_color": "Polar White",
        "interior_color": "Black",
        "dealer": "Mercedes-Benz of San Francisco",
        "city": "San Francisco",
        "state": "CA",
        "primaryImage": "https://example.com/mercedes-sprinter.jpg",
        "vdp": "https://example.com/vehicle/1GCVKREC5JZ123456"
    },
    {
        "vin": "1HGBH41JXMN109187",
        "year": 2023,
        "make": "Honda",
        "model": "CR-V",
        "trim": "EX-L",
        "body_style": "SUV",
        "price": 34900.00,
        "mileage": 5000,
        "fuel_type": "Hybrid",
        "drivetrain": "AWD",
        "transmission": "Automatic",
        "exterior_color": "Radiant Red Metallic",
        "interior_color": "Black",
        "dealer": "Honda of San Francisco",
        "city": "San Francisco",
        "state": "CA",
        "primaryImage": "https://example.com/honda-crv.jpg",
        "vdp": "https://example.com/vehicle/1HGBH41JXMN109187"
    },
    {
        "vin": "5YJSA1E11HF123789",
        "year": 2024,
        "make": "Tesla",
        "model": "Model Y",
        "trim": "Long Range",
        "body_style": "SUV",
        "price": 52990.00,
        "mileage": 1000,
        "fuel_type": "Electric",
        "drivetrain": "AWD",
        "transmission": "Automatic",
        "exterior_color": "Midnight Silver Metallic",
        "interior_color": "Black",
        "dealer": "Tesla Store San Francisco",
        "city": "San Francisco",
        "state": "CA",
        "primaryImage": "https://example.com/tesla-modely.jpg",
        "vdp": "https://example.com/vehicle/5YJSA1E11HF123789"
    },
    {
        "vin": "1FTFW1E55MFC12346",
        "year": 2023,
        "make": "Ford",
        "model": "F-150",
        "trim": "Lariat",
        "body_style": "Truck",
        "price": 58900.00,
        "mileage": 3000,
        "fuel_type": "Hybrid",
        "drivetrain": "4WD",
        "transmission": "Automatic",
        "exterior_color": "Agate Black",
        "interior_color": "Black",
        "dealer": "Ford of San Jose",
        "city": "San Jose",
        "state": "CA",
        "primaryImage": "https://example.com/ford-f150-lariat.jpg",
        "vdp": "https://example.com/vehicle/1FTFW1E55MFC12346"
    },
    {
        "vin": "WBA3A5C59EK123457",
        "year": 2023,
        "make": "BMW",
        "model": "X5",
        "trim": "xDrive50e",
        "body_style": "SUV",
        "price": 72900.00,
        "mileage": 2000,
        "fuel_type": "Plug-in Hybrid",
        "drivetrain": "AWD",
        "transmission": "Automatic",
        "exterior_color": "Phytonic Blue Metallic",
        "interior_color": "Black",
        "dealer": "BMW of San Francisco",
        "city": "San Francisco",
        "state": "CA",
        "primaryImage": "https://example.com/bmw-x5-phev.jpg",
        "vdp": "https://example.com/vehicle/WBA3A5C59EK123457"
    },
    {
        "vin": "JTMB1RFV8KD123457",
        "year": 2024,
        "make": "Toyota",
        "model": "RAV4",
        "trim": "Limited",
        "body_style": "SUV",
        "price": 38900.00,
        "mileage": 500,
        "fuel_type": "Hybrid",
        "drivetrain": "AWD",
        "transmission": "Automatic",
        "exterior_color": "Lunar Rock",
        "interior_color": "Softex Black",
        "dealer": "Toyota of Oakland",
        "city": "Oakland",
        "state": "CA",
        "primaryImage": "https://example.com/toyota-rav4-limited.jpg",
        "vdp": "https://example.com/vehicle/JTMB1RFV8KD123457"
    },
    {
        "vin": "1C4HJXDN8MW123457",
        "year": 2023,
        "make": "Jeep",
        "model": "Wrangler",
        "trim": "Rubicon",
        "body_style": "SUV",
        "price": 52900.00,
        "mileage": 4000,
        "fuel_type": "Gasoline",
        "drivetrain": "4WD",
        "transmission": "Automatic",
        "exterior_color": "Sting-Gray",
        "interior_color": "Black",
        "dealer": "Jeep of San Francisco",
        "city": "San Francisco",
        "state": "CA",
        "primaryImage": "https://example.com/jeep-wrangler-rubicon.jpg",
        "vdp": "https://example.com/vehicle/1C4HJXDN8MW123457"
    },
    {
        "vin": "5N1AR2MM9FC123457",
        "year": 2022,
        "make": "Nissan",
        "model": "Altima",
        "trim": "SL",
        "body_style": "Sedan",
        "price": 31900.00,
        "mileage": 12000,
        "fuel_type": "Gasoline",
        "drivetrain": "FWD",
        "transmission": "Automatic",
        "exterior_color": "Scarlet Ember Tintcoat",
        "interior_color": "Charcoal",
        "dealer": "Nissan of San Francisco",
        "city": "San Francisco",
        "state": "CA",
        "primaryImage": "https://example.com/nissan-altima-sl.jpg",
        "vdp": "https://example.com/vehicle/5N1AR2MM9FC123457"
    },
    {
        "vin": "1G1BE5SM9K7123457",
        "year": 2024,
        "make": "Chevrolet",
        "model": "Camaro",
        "trim": "SS",
        "body_style": "Coupe",
        "price": 45900.00,
        "mileage": 1000,
        "fuel_type": "Gasoline",
        "drivetrain": "RWD",
        "transmission": "Manual",
        "exterior_color": "Rally Green",
        "interior_color": "Black",
        "dealer": "Chevrolet of San Francisco",
        "city": "San Francisco",
        "state": "CA",
        "primaryImage": "https://example.com/chevrolet-camaro-ss.jpg",
        "vdp": "https://example.com/vehicle/1G1BE5SM9K7123457"
    },
    {
        "vin": "4T1B11HK5JU123457",
        "year": 2023,
        "make": "Toyota",
        "model": "Camry",
        "trim": "XSE V6",
        "body_style": "Sedan",
        "price": 36900.00,
        "mileage": 8000,
        "fuel_type": "Gasoline",
        "drivetrain": "FWD",
        "transmission": "Automatic",
        "exterior_color": "Midnight Black Metallic",
        "interior_color": "Red",
        "dealer": "Toyota of San Francisco",
        "city": "San Francisco",
        "state": "CA",
        "primaryImage": "https://example.com/toyota-camry-xse.jpg",
        "vdp": "https://example.com/vehicle/4T1B11HK5JU123457"
    },
    {
        "vin": "WAUAF48H17K123457",
        "year": 2024,
        "make": "Audi",
        "model": "A4",
        "trim": "Prestige",
        "body_style": "Sedan",
        "price": 51900.00,
        "mileage": 500,
        "fuel_type": "Gasoline",
        "drivetrain": "AWD",
        "transmission": "Automatic",
        "exterior_color": "Glacier White",
        "interior_color": "Black",
        "dealer": "Audi of San Francisco",
        "city": "San Francisco",
        "state": "CA",
        "primaryImage": "https://example.com/audi-a4-prestige.jpg",
        "vdp": "https://example.com/vehicle/WAUAF48H17K123457"
    },
    {
        "vin": "5YJ3E1EA1KF123457",
        "year": 2024,
        "make": "Tesla",
        "model": "Model S",
        "trim": "Plaid",
        "body_style": "Sedan",
        "price": 89990.00,
        "mileage": 500,
        "fuel_type": "Electric",
        "drivetrain": "AWD",
        "transmission": "Automatic",
        "exterior_color": "Pearl White",
        "interior_color": "Black",
        "dealer": "Tesla Store Palo Alto",
        "city": "Palo Alto",
        "state": "CA",
        "primaryImage": "https://example.com/tesla-models-plaid.jpg",
        "vdp": "https://example.com/vehicle/5YJ3E1EA1KF123457"
    },
    {
        "vin": "1FTBW3CM9HKA12346",
        "year": 2024,
        "make": "Ford",
        "model": "Transit",
        "trim": "350 XLT High Roof",
        "body_style": "Van",
        "price": 49900.00,
        "mileage": 1000,
        "fuel_type": "Gasoline",
        "drivetrain": "RWD",
        "transmission": "Automatic",
        "exterior_color": "Oxford White",
        "interior_color": "Gray",
        "dealer": "Ford Commercial San Jose",
        "city": "San Jose",
        "state": "CA",
        "primaryImage": "https://example.com/ford-transit-highroof.jpg",
        "vdp": "https://example.com/vehicle/1FTBW3CM9HKA12346"
    },
    {
        "vin": "1GCVKREC5JZ123457",
        "year": 2023,
        "make": "Mercedes-Benz",
        "model": "Sprinter",
        "trim": "2500 Crew High Roof",
        "body_style": "Van",
        "price": 59900.00,
        "mileage": 5000,
        "fuel_type": "Diesel",
        "drivetrain": "RWD",
        "transmission": "Automatic",
        "exterior_color": "Polar White",
        "interior_color": "Black",
        "dealer": "Mercedes-Benz of San Francisco",
        "city": "San Francisco",
        "state": "CA",
        "primaryImage": "https://example.com/mercedes-sprinter-highroof.jpg",
        "vdp": "https://example.com/vehicle/1GCVKREC5JZ123457"
    },
    {
        "vin": "1HGBH41JXMN109188",
        "year": 2022,
        "make": "Honda",
        "model": "Civic",
        "trim": "Sport",
        "body_style": "Sedan",
        "price": 24900.00,
        "mileage": 18000,
        "fuel_type": "Gasoline",
        "drivetrain": "FWD",
        "transmission": "Automatic",
        "exterior_color": "Sonic Gray Pearl",
        "interior_color": "Black",
        "dealer": "Honda of San Francisco",
        "city": "San Francisco",
        "state": "CA",
        "primaryImage": "https://example.com/honda-civic-sport.jpg",
        "vdp": "https://example.com/vehicle/1HGBH41JXMN109188"
    },
    {
        "vin": "5YJ3E1EA1KF123458",
        "year": 2023,
        "make": "Tesla",
        "model": "Model X",
        "trim": "Plaid",
        "body_style": "SUV",
        "price": 94990.00,
        "mileage": 3000,
        "fuel_type": "Electric",
        "drivetrain": "AWD",
        "transmission": "Automatic",
        "exterior_color": "Pearl White",
        "interior_color": "Cream",
        "dealer": "Tesla Store Palo Alto",
        "city": "Palo Alto",
        "state": "CA",
        "primaryImage": "https://example.com/tesla-modelx-plaid.jpg",
        "vdp": "https://example.com/vehicle/5YJ3E1EA1KF123458"
    }
]

def create_database():
    """Create the vehicle database with sample data."""
    print(f"Creating vehicle database at: {DB_PATH}")
    
    # Remove existing database if it exists
    if DB_PATH.exists():
        print(f"Removing existing database...")
        DB_PATH.unlink()
    
    # Create database connection
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    # Create unified_vehicle_listings table (schema expected by IDSS vehicle_store)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS unified_vehicle_listings (
            vin TEXT PRIMARY KEY,
            year INTEGER,
            make TEXT,
            model TEXT,
            trim TEXT,
            body_style TEXT,
            price REAL,
            mileage INTEGER,
            fuel_type TEXT,
            drivetrain TEXT,
            engine TEXT,
            transmission TEXT,
            doors INTEGER,
            seats INTEGER,
            exterior_color TEXT,
            interior_color TEXT,
            dealer_name TEXT,
            dealer_city TEXT,
            dealer_state TEXT,
            dealer_zip TEXT,
            dealer_latitude REAL,
            dealer_longitude REAL,
            is_used INTEGER,
            is_cpo INTEGER,
            vdp_url TEXT,
            carfax_url TEXT,
            primary_image_url TEXT,
            photo_count INTEGER,
            build_city_mpg INTEGER,
            build_highway_mpg INTEGER,
            norm_body_type TEXT,
            norm_fuel_type TEXT,
            norm_is_used INTEGER,
            raw_json TEXT
        )
    """)
    
    # Create indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_make_model ON unified_vehicle_listings(make, model)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_body_style ON unified_vehicle_listings(body_style)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_price ON unified_vehicle_listings(price)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_year ON unified_vehicle_listings(year)")
    
    # Insert sample vehicles
    print(f"Inserting {len(SAMPLE_VEHICLES)} sample vehicles...")
    for vehicle in SAMPLE_VEHICLES:
        # Create raw_json for compatibility
        raw_json = json.dumps({
            "vin": vehicle["vin"],
            "year": vehicle["year"],
            "make": vehicle["make"],
            "model": vehicle["model"],
            "trim": vehicle.get("trim"),
            "bodyStyle": vehicle["body_style"],
            "price": vehicle["price"],
            "mileage": vehicle["mileage"],
            "fuelType": vehicle["fuel_type"],
            "drivetrain": vehicle["drivetrain"],
            "transmission": vehicle["transmission"],
            "exteriorColor": vehicle["exterior_color"],
            "interiorColor": vehicle["interior_color"],
            "dealer": vehicle["dealer"],
            "city": vehicle["city"],
            "state": vehicle["state"],
            "primaryImage": vehicle.get("primaryImage"),
            "vdp": vehicle.get("vdp"),
        })
        
        cursor.execute("""
            INSERT INTO unified_vehicle_listings (
                vin, year, make, model, trim, body_style, price, mileage,
                fuel_type, drivetrain, engine, transmission, doors, seats,
                exterior_color, interior_color,
                dealer_name, dealer_city, dealer_state, dealer_zip,
                dealer_latitude, dealer_longitude,
                is_used, is_cpo, vdp_url, carfax_url,
                primary_image_url, photo_count,
                build_city_mpg, build_highway_mpg,
                norm_body_type, norm_fuel_type, norm_is_used, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            vehicle["vin"],
            vehicle["year"],
            vehicle["make"],
            vehicle["model"],
            vehicle.get("trim"),
            vehicle["body_style"],
            vehicle["price"],
            vehicle["mileage"],
            vehicle["fuel_type"],
            vehicle["drivetrain"],
            None,  # engine
            vehicle["transmission"],
            4 if vehicle["body_style"] in ["Sedan", "Coupe"] else 5,  # doors (estimate)
            5 if vehicle["body_style"] in ["Sedan", "Coupe"] else (7 if vehicle["body_style"] == "SUV" else 8),  # seats (estimate)
            vehicle["exterior_color"],
            vehicle["interior_color"],
            vehicle["dealer"],
            vehicle["city"],
            vehicle["state"],
            None,  # dealer_zip
            37.7749,  # dealer_latitude (San Francisco default)
            -122.4194,  # dealer_longitude (San Francisco default)
            1 if vehicle["mileage"] > 0 else 0,  # is_used
            0,  # is_cpo
            vehicle.get("vdp"),
            None,  # carfax_url
            vehicle.get("primaryImage"),
            1 if vehicle.get("primaryImage") else 0,  # photo_count
            None,  # build_city_mpg
            None,  # build_highway_mpg
            vehicle["body_style"],  # norm_body_type
            vehicle["fuel_type"],    # norm_fuel_type
            1 if vehicle["mileage"] > 0 else 0,  # norm_is_used
            raw_json
        ))
    
    conn.commit()
    conn.close()
    
    print(f"[OK] Database created successfully!")
    print(f"   Location: {DB_PATH}")
    print(f"   Vehicles: {len(SAMPLE_VEHICLES)}")
    print(f"   Size: {DB_PATH.stat().st_size / 1024:.2f} KB")

if __name__ == "__main__":
    create_database()
