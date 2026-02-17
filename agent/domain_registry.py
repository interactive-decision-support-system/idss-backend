"""
Domain Registry for IDSS Unified Pipeline.

Defines the structure for domain selection criteria (schemas) and provides
a registry of available domains (vehicles, laptops, books).

This replaces hardcoded interview logic with a data-driven approach.
"""
from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class SlotPriority(str, Enum):
    """
    Priority level for preference slots.
    Determines the order in which the agent asks questions.
    """
    HIGH = "HIGH"      # Critical (Budget, Use Case) - Ask first
    MEDIUM = "MEDIUM"  # Important (Features, Brand) - Ask next
    LOW = "LOW"        # Nice to have (Color, New/Used) - Ask if time permits


class PreferenceSlot(BaseModel):
    """
    Definition of a single preference slot (a criterion to ask about).
    """
    name: str = Field(..., description="Internal key for the slot (e.g. 'budget')")
    display_name: str = Field(..., description="Human-readable name (e.g. 'Budget')")
    priority: SlotPriority = Field(..., description="Priority level for asking")
    description: str = Field(..., description="Description for the LLM to understand this slot")
    example_question: str = Field(..., description="Example question the agent might ask")
    example_replies: List[str] = Field(default_factory=list, description="Suggestions for quick replies")
    
    # Optional mapping to filter keys if direct mapping exists
    filter_key: Optional[str] = None
    # Allowed values for categorical filters (agent MUST use one of these exact values)
    allowed_values: Optional[List[str]] = None


class DomainSchema(BaseModel):
    """
    Complete schema for a domain, defining what to ask and in what order.
    """
    domain: str = Field(..., description="Domain identifier (e.g. 'vehicles', 'laptops')")
    description: str = Field(..., description="Description of the domain for the router")
    slots: List[PreferenceSlot] = Field(..., description="List of preference slots for this domain")
    
    def get_slots_by_priority(self) -> Dict[SlotPriority, List[PreferenceSlot]]:
        """Returns slots grouped by priority."""
        return {
            SlotPriority.HIGH: [s for s in self.slots if s.priority == SlotPriority.HIGH],
            SlotPriority.MEDIUM: [s for s in self.slots if s.priority == SlotPriority.MEDIUM],
            SlotPriority.LOW: [s for s in self.slots if s.priority == SlotPriority.LOW],
        }


# ============================================================================
# Domain Definitions
# ============================================================================

# 1. Vehicles Schema (Matching IDSS vehicle logic)
VEHICLE_SCHEMA = DomainSchema(
    domain="vehicles",
    description="Cars, trucks, SUVs, and other vehicles for sale.",
    slots=[
        PreferenceSlot(
            name="budget",
            display_name="Budget",
            priority=SlotPriority.HIGH,
            description="The price range or maximum price the user is willing to pay.",
            example_question="What is your price range for the vehicle?",
            example_replies=["Under $20k", "$20k-$35k", "$35k-$50k", "Over $50k"],
            filter_key="price_max"
        ),
        PreferenceSlot(
            name="use_case",
            display_name="Primary Use",
            priority=SlotPriority.HIGH,
            description="What the user intends to use the vehicle for (commuting, family, off-road, etc.).",
            example_question="What will you primarily use the vehicle for?",
            example_replies=["Commuting", "Family trips", "Off-road adventures", "Work truck"]
        ),
        PreferenceSlot(
            name="body_style",
            display_name="Body Style",
            priority=SlotPriority.HIGH,
            description="The physical shape or category of the car.",
            example_question="Do you have a preference for a specific body style?",
            example_replies=["SUV", "Sedan", "Pickup", "Hatchback"],
            filter_key="body_style",
            allowed_values=["SUV", "Pickup", "Sedan", "Hatchback", "Coupe", "Convertible", "Minivan", "Cargo Van", "Wagon", "Passenger Van"]
        ),
        PreferenceSlot(
            name="features",
            display_name="Key Features",
            priority=SlotPriority.MEDIUM,
            description="Specific features the user likes (leather seats, sunroof, navigation, etc.).",
            example_question="Are there any specific features explicitly must have?",
            example_replies=["Fuel efficiency", "Safety features", "Apple CarPlay", "Leather seats"]
        ),
        PreferenceSlot(
            name="brand",
            display_name="Brand",
            priority=SlotPriority.MEDIUM,
            description="Preferred manufacturer.",
            example_question="Do you have a preferred car brand?",
            example_replies=["Toyota", "Honda", "Ford", "No preference"],
            filter_key="make",
            allowed_values=["Ford", "Chevrolet", "Honda", "Jeep", "Toyota", "Ram", "BMW", "Cadillac", "Acura", "Hyundai", "GMC", "Nissan", "Mercedes-Benz", "Kia", "Volkswagen", "Subaru", "Dodge", "Audi", "Tesla", "Volvo", "Mazda", "Lexus", "Buick", "Porsche", "Chrysler", "Land Rover", "MINI", "Mitsubishi", "Lincoln"]
        ),
        PreferenceSlot(
            name="fuel_type",
            display_name="Fuel Type",
            priority=SlotPriority.LOW,
            description="Engine/fuel type.",
            example_question="Do you prefer a specific fuel type?",
            example_replies=["Gasoline", "Hybrid", "Electric", "No preference"],
            filter_key="fuel_type",
            allowed_values=["Gasoline", "Diesel", "Electric", "Hybrid (Electric + Gasoline)", "Hydrogen"]
        ),
        PreferenceSlot(
            name="condition",
            display_name="New vs Used",
            priority=SlotPriority.LOW,
            description="Condition of the car (New or Used).",
            example_question="Are you looking for new or used?",
            example_replies=["New", "Used", "Either"],
            filter_key="is_used"
        )
    ]
)

# 2. Laptops Schema
LAPTOP_SCHEMA = DomainSchema(
    domain="laptops",
    description="Laptops, notebooks, and portable computers.",
    slots=[
        PreferenceSlot(
            name="use_case",
            display_name="Primary Use",
            priority=SlotPriority.HIGH,
            description="Primary activity (Gaming, Work, School, Creative).",
            example_question="What will you primarily use the laptop for?",
            example_replies=["Gaming", "Work/Business", "School/Student", "Creative Work"]
        ),
        PreferenceSlot(
            name="budget",
            display_name="Budget",
            priority=SlotPriority.HIGH,
            description="Price range for the laptop.",
            example_question="What is your budget for the laptop?",
            example_replies=["Under $700", "$700-$1200", "$1200-$2000", "Over $2000"],
            filter_key="price_max_cents"
        ),
        PreferenceSlot(
            name="brand",
            display_name="Brand",
            priority=SlotPriority.MEDIUM,
            description="Preferred manufacturer (Apple, Dell, HP, etc.).",
            example_question="Do you have a preferred brand?",
            example_replies=["Apple (Mac)", "Dell", "Lenovo", "HP"],
            filter_key="brand"
        ),
        PreferenceSlot(
            name="os",
            display_name="Operating System",
            priority=SlotPriority.MEDIUM,
            description="Preferred OS (macOS, Windows, ChromeOS).",
            example_question="Do you prefer Mac or Windows?",
            example_replies=["macOS", "Windows", "ChromeOS", "No preference"]
        ),
        PreferenceSlot(
            name="screen_size",
            display_name="Screen Size",
            priority=SlotPriority.LOW,
            description="Preferred screen size (13-inch, 15-inch, etc.).",
            example_question="What screen size do you prefer?",
            example_replies=["13-14 inch (Portable)", "15-16 inch (Standard)", "17+ inch (Large)"]
        )
    ]
)

# 3. Jewelry Schema
JEWELRY_SCHEMA = DomainSchema(
    domain="jewelry",
    description="Jewelry: necklaces, earrings, bracelets, rings, pendants.",
    slots=[
        PreferenceSlot(
            name="item_type",
            display_name="Type",
            priority=SlotPriority.HIGH,
            description="Type of jewelry (necklace, earrings, bracelet, ring, pendant).",
            example_question="What type of jewelry are you looking for?",
            example_replies=["Necklace", "Earrings", "Bracelet", "Ring", "Pendant"],
            filter_key="subcategory"
        ),
        PreferenceSlot(
            name="budget",
            display_name="Budget",
            priority=SlotPriority.HIGH,
            description="Price range for the jewelry.",
            example_question="What is your budget?",
            example_replies=["Under $50", "$50-$150", "$150-$300", "Over $300"],
            filter_key="price_max_cents"
        ),
        PreferenceSlot(
            name="brand",
            display_name="Brand",
            priority=SlotPriority.MEDIUM,
            description="Preferred jewelry brand (Pandora, Tiffany, Swarovski, etc.).",
            example_question="Do you have a preferred brand?",
            example_replies=["Pandora", "Tiffany & Co", "Swarovski", "Kay Jewelers", "No preference"],
            filter_key="brand"
        ),
        PreferenceSlot(
            name="material",
            display_name="Material",
            priority=SlotPriority.LOW,
            description="Preferred material (gold, silver, etc.).",
            example_question="Do you prefer a specific material?",
            example_replies=["Gold", "Silver", "Rose Gold", "No preference"]
        )
    ]
)

# 4. Accessories Schema
ACCESSORIES_SCHEMA = DomainSchema(
    domain="accessories",
    description="Accessories: scarves, hats, belts, bags, watches, sunglasses.",
    slots=[
        PreferenceSlot(
            name="item_type",
            display_name="Type",
            priority=SlotPriority.HIGH,
            description="Type of accessory (scarf, hat, belt, bag, watch, sunglasses).",
            example_question="What type of accessory are you looking for?",
            example_replies=["Scarf", "Hat", "Belt", "Bag", "Watch", "Sunglasses"],
            filter_key="subcategory"
        ),
        PreferenceSlot(
            name="budget",
            display_name="Budget",
            priority=SlotPriority.HIGH,
            description="Price range for the accessory.",
            example_question="What is your budget?",
            example_replies=["Under $50", "$50-$150", "$150-$300", "Over $300"],
            filter_key="price_max_cents"
        ),
        PreferenceSlot(
            name="brand",
            display_name="Brand",
            priority=SlotPriority.MEDIUM,
            description="Preferred brand.",
            example_question="Do you have a preferred brand?",
            example_replies=["No preference", "Pandora", "Tiffany & Co", "Swarovski"]
        )
    ]
)

# 5. Clothing Schema
CLOTHING_SCHEMA = DomainSchema(
    domain="clothing",
    description="Clothing and apparel: dresses, shirts, pants, jackets, etc.",
    slots=[
        PreferenceSlot(
            name="item_type",
            display_name="Type",
            priority=SlotPriority.HIGH,
            description="Type of clothing (dresses, shirts, pants, jackets, etc.).",
            example_question="What type of clothing are you looking for?",
            example_replies=["Dresses", "Shirts & Blouses", "Pants", "Graphic Tees", "Shorts", "Jackets"],
            filter_key="subcategory"
        ),
        PreferenceSlot(
            name="budget",
            display_name="Budget",
            priority=SlotPriority.HIGH,
            description="Price range for the clothing.",
            example_question="What is your budget?",
            example_replies=["Under $50", "$50-$100", "$100-$200", "Over $200"],
            filter_key="price_max_cents"
        ),
        PreferenceSlot(
            name="brand",
            display_name="Brand",
            priority=SlotPriority.MEDIUM,
            description="Preferred clothing brand.",
            example_question="Do you have a preferred brand?",
            example_replies=["No preference", "Nike", "Patagonia", "Uniqlo"],
            filter_key="brand"
        ),
        PreferenceSlot(
            name="color",
            display_name="Color",
            priority=SlotPriority.LOW,
            description="Preferred color.",
            example_question="Do you prefer a specific color?",
            example_replies=["Black", "White", "Blue", "Navy", "Gray", "No preference"]
        )
    ]
)

# 6. Beauty Schema
BEAUTY_SCHEMA = DomainSchema(
    domain="beauty",
    description="Beauty and cosmetics: lipstick, eyeshadow, skincare, etc.",
    slots=[
        PreferenceSlot(
            name="item_type",
            display_name="Type",
            priority=SlotPriority.HIGH,
            description="Type of beauty product (lipstick, eyeshadow, skincare, etc.).",
            example_question="What type of beauty product are you looking for?",
            example_replies=["Lipstick", "Eyeshadow", "Mascara", "Skincare", "Foundation", "Blush"],
            filter_key="subcategory"
        ),
        PreferenceSlot(
            name="budget",
            display_name="Budget",
            priority=SlotPriority.HIGH,
            description="Price range for the beauty product.",
            example_question="What is your budget?",
            example_replies=["Under $20", "$20-$50", "$50-$100", "Over $100"],
            filter_key="price_max_cents"
        ),
        PreferenceSlot(
            name="brand",
            display_name="Brand",
            priority=SlotPriority.MEDIUM,
            description="Preferred beauty brand.",
            example_question="Do you have a preferred brand?",
            example_replies=["No preference", "MAC", "NARS", "ColourPop", "Fenty Beauty"],
            filter_key="brand"
        ),
        PreferenceSlot(
            name="color",
            display_name="Color/Shade",
            priority=SlotPriority.LOW,
            description="Preferred color or shade.",
            example_question="Do you prefer a specific color or shade?",
            example_replies=["Red", "Nude", "Pink", "Neutral", "No preference"]
        )
    ]
)

# 7. Books Schema
BOOK_SCHEMA = DomainSchema(
    domain="books",
    description="Fiction and non-fiction books, novels, and literature.",
    slots=[
        PreferenceSlot(
            name="genre",
            display_name="Genre",
            priority=SlotPriority.HIGH,
            description="The category or genre of the book (Fiction, Mystery, Sci-Fi, etc.).",
            example_question="What genre of book are you in the mood for?",
            example_replies=["Fiction", "Mystery", "Sci-Fi", "Non-Fiction", "Self-Help"],
            filter_key="genre"
        ),
        PreferenceSlot(
            name="format",
            display_name="Format",
            priority=SlotPriority.MEDIUM,
            description="Physical format (Hardcover, Paperback, E-book).",
            example_question="Do you prefer a specific format?",
            example_replies=["Hardcover", "Paperback", "E-book", "Audiobook"],
            filter_key="format"
        ),
        PreferenceSlot(
            name="budget",
            display_name="Budget",
            priority=SlotPriority.LOW,  # Lower priority for books usually
            description="Maximum price for the book.",
            example_question="Do you have a price limit?",
            example_replies=["Under $15", "$15-$30", "Any price"],
            filter_key="price_max_cents"
        )
    ]
)


# ============================================================================
# Registry Access
# ============================================================================

DOMAIN_REGISTRY = {
    VEHICLE_SCHEMA.domain: VEHICLE_SCHEMA,
    LAPTOP_SCHEMA.domain: LAPTOP_SCHEMA,
    JEWELRY_SCHEMA.domain: JEWELRY_SCHEMA,
    ACCESSORIES_SCHEMA.domain: ACCESSORIES_SCHEMA,
    CLOTHING_SCHEMA.domain: CLOTHING_SCHEMA,
    BEAUTY_SCHEMA.domain: BEAUTY_SCHEMA,
    BOOK_SCHEMA.domain: BOOK_SCHEMA
}

def get_domain_schema(domain: str) -> Optional[DomainSchema]:
    """Retrieves the schema for a given domain."""
    return DOMAIN_REGISTRY.get(domain)

def list_domains() -> List[str]:
    """Returns a list of available domain names."""
    return list(DOMAIN_REGISTRY.keys())
