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
            description="The physical shape or category of the car (SUV, Sedan, Truck, etc.).",
            example_question="Do you have a preference for a specific body style?",
            example_replies=["SUV", "Sedan", "Truck", "Crossover"],
            filter_key="body_style"
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
            description="Preferred manufacturer (Toyota, Ford, BMW, etc.).",
            example_question="Do you have a preferred car brand?",
            example_replies=["Toyota", "Honda", "Ford", "No preference"],
            filter_key="make"
        ),
        PreferenceSlot(
            name="fuel_type",
            display_name="Fuel Type",
            priority=SlotPriority.LOW,
            description="Engine type (Gas, Hybrid, Electric).",
            example_question="Do you prefer a specific fuel type?",
            example_replies=["Gas only", "Hybrid", "Electric", "No preference"],
            filter_key="fuel_type"
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
            description="Preferred OS (macOS, Windows, Linux, ChromeOS).",
            example_question="Do you prefer Mac, Windows, or Linux?",
            example_replies=["macOS", "Windows", "Linux", "ChromeOS", "No preference"]
        ),
        PreferenceSlot(
            name="screen_size",
            display_name="Screen Size",
            priority=SlotPriority.LOW,
            description="Preferred screen size (13-inch, 15-inch, etc.).",
            example_question="What screen size do you prefer?",
            example_replies=["13-14 inch (Portable)", "15-16 inch (Standard)", "17+ inch (Large)"]
        ),
        # Richer KG (§7): Reddit-style features for complex queries (good for ML, battery life, etc.)
        PreferenceSlot(
            name="good_for_ml",
            display_name="Good for ML / Deep Learning",
            priority=SlotPriority.LOW,
            description="Whether the laptop is suitable for machine learning or deep learning (e.g. dedicated GPU, 16GB+ RAM).",
            example_question="Do you need it for machine learning or deep learning?",
            example_replies=["Yes", "No", "Nice to have"],
            filter_key="good_for_ml"
        ),
        PreferenceSlot(
            name="good_for_web_dev",
            display_name="Good for Web Development",
            priority=SlotPriority.LOW,
            description="Suitable for web development (coding, IDEs, multiple browsers).",
            example_question="Will you use it for web development?",
            example_replies=["Yes", "No"],
            filter_key="good_for_web_dev"
        ),
        PreferenceSlot(
            name="battery_life",
            display_name="Battery Life",
            priority=SlotPriority.LOW,
            description="Minimum battery life in hours (e.g. 8+ hours).",
            example_question="How many hours of battery life do you need?",
            example_replies=["6+ hours", "8+ hours", "10+ hours", "No preference"],
            filter_key="battery_life_min_hours"
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
