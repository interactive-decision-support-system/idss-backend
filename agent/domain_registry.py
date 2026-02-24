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
            example_question="Are there any specific features that are a must have?",
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

# 2. Electronics Schema (laptops only — real Supabase attribute keys confirmed)
# DB attributes keys: ram_gb (int), screen_size (inches int), storage_gb (int),
# storage_type ('SSD'/'HDD'), battery_life_hours (int), cpu (str),
# refresh_rate_hz (int), good_for_ml/gaming/creative/web_dev (bool)
LAPTOP_SCHEMA = DomainSchema(
    domain="laptops",
    description="Laptops and notebooks for all use cases.",
    slots=[
        PreferenceSlot(
            name="use_case",
            display_name="Primary Use",
            priority=SlotPriority.HIGH,
            description=(
                "What the user will primarily use the laptop for.  "
                "Map to one of: 'gaming', 'machine_learning', 'creative', 'web_dev', 'school', 'business', 'general'. "
                "This is used for soft ranking via good_for_* boolean attributes."
            ),
            example_question="What will you mainly use the laptop for?",
            example_replies=["Gaming", "Machine learning / AI", "Creative work (design/video)", "Work / Business", "School / Student"]
        ),
        PreferenceSlot(
            name="budget",
            display_name="Budget",
            priority=SlotPriority.HIGH,
            description="Price range for the laptop. Extract as a max price (e.g. '$1500') or a range ('$1000-$2000').",
            example_question="What is your budget?",
            example_replies=["Under $800", "$800-$1500", "$1500-$2500", "Over $2500"],
            filter_key="price_max_cents"
        ),
        PreferenceSlot(
            name="min_ram_gb",
            display_name="Minimum RAM",
            priority=SlotPriority.MEDIUM,
            description=(
                "Minimum RAM in GB the user requires. Extract as an integer (e.g. 16, 32). "
                "Common values in DB: 8, 16, 32, 64. Maps to attributes->ram_gb in Supabase."
            ),
            example_question="How much RAM do you need at minimum?",
            example_replies=["8 GB", "16 GB", "32 GB", "No preference"],
            filter_key="min_ram_gb"
        ),
        PreferenceSlot(
            name="screen_size",
            display_name="Screen Size",
            priority=SlotPriority.MEDIUM,
            description=(
                "Screen size preference in inches. Extract the FULL user intent as a string, e.g.: "
                "'at least 15' (minimum), 'under 14' or 'small/compact' (maximum), "
                "'exactly 15.6' (exact match ±0.5\"), '14 to 16' or '14-16' (range). "
                "Common DB values: 13, 14, 15, 16. DB column: attributes->screen_size."
            ),
            example_question="Do you have a screen size preference?",
            example_replies=["13\" (compact)", "14\"", "15.6\"", "16\" (large)", "No preference"],
            filter_key="screen_size"
        ),
        PreferenceSlot(
            name="brand",
            display_name="Brand",
            priority=SlotPriority.MEDIUM,
            description="Preferred laptop manufacturer.",
            example_question="Do you have a preferred brand?",
            example_replies=["No preference", "Apple", "Dell", "Lenovo", "ASUS", "HP"],
            filter_key="brand",
            allowed_values=[
                "Apple", "Dell", "Lenovo", "HP", "ASUS", "MSI", "Razer",
                "Acer", "Microsoft", "Samsung", "LG", "Gigabyte",
                # Direct-seller brands scraped into Supabase
                "Framework", "System76", "ROG", "Alienware",
            ]
        ),
        PreferenceSlot(
            name="storage_type",
            display_name="Storage Type",
            priority=SlotPriority.LOW,
            description="Preferred storage type. DB values: 'SSD' or 'HDD'. Maps to attributes->storage_type.",
            example_question="Do you prefer SSD or HDD storage?",
            example_replies=["SSD (fast)", "HDD (large capacity)", "No preference"],
            filter_key="storage_type",
            allowed_values=["SSD", "HDD"]
        ),
    ]
)


# 3. Phones Schema (real scraped: Fairphone, BigCommerce, etc.)
PHONES_SCHEMA = DomainSchema(
    domain="phones",
    description="Phones and smartphones (repairable, sustainable, budget).",
    slots=[
        PreferenceSlot(
            name="budget",
            display_name="Budget",
            priority=SlotPriority.HIGH,
            description="Price range for the phone.",
            example_question="What is your budget for a phone?",
            example_replies=["Under $300", "$300-$500", "$500-$800", "Over $800"],
            filter_key="price_max_cents"
        ),
        PreferenceSlot(
            name="brand",
            display_name="Brand",
            priority=SlotPriority.MEDIUM,
            description="Preferred phone brand (Fairphone, Apple, Samsung, etc.).",
            example_question="Do you have a preferred brand?",
            example_replies=["Fairphone", "No preference", "Apple", "Samsung"],
            filter_key="brand"
        ),
    ]
)

# 4. Books Schema
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

# Only: vehicles, laptops, books, phones (real scraped products)
DOMAIN_REGISTRY = {
    VEHICLE_SCHEMA.domain: VEHICLE_SCHEMA,
    LAPTOP_SCHEMA.domain: LAPTOP_SCHEMA,
    BOOK_SCHEMA.domain: BOOK_SCHEMA,
    PHONES_SCHEMA.domain: PHONES_SCHEMA,
}

def get_domain_schema(domain: str) -> Optional[DomainSchema]:
    """Retrieves the schema for a given domain."""
    return DOMAIN_REGISTRY.get(domain)

def list_domains() -> List[str]:
    """Returns a list of available domain names."""
    return list(DOMAIN_REGISTRY.keys())
