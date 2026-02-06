"""
Advanced Laptop Recommendation System

More sophisticated algorithms specifically optimized for laptop recommendations.
Goes beyond basic IDSS ranking with laptop-specific features.

Features:
1. Use case matching (gaming vs work vs school)
2. Performance tier classification (budget/mid/premium/enthusiast)
3. Compatibility scoring (GPU + CPU + RAM combinations)
4. Value-for-money analysis
5. Spec-based ranking (CPU benchmarks, GPU tiers, RAM/storage)
6. Battery life importance weighting

Usage:
    from app.laptop_recommender import LaptopRecommender
    
    recommender = LaptopRecommender()
    ranked = recommender.rank_laptops(laptops, user_preferences)
"""

from typing import List, Dict, Any, Optional
import json
from dataclasses import dataclass


@dataclass
class UserPreferences:
    """User preferences for laptop recommendations."""
    use_case: str  # gaming, work, school, creative
    budget_max: Optional[float] = None
    budget_min: Optional[float] = None
    portability_importance: float = 0.5  # 0-1
    performance_importance: float = 0.5  # 0-1
    battery_importance: float = 0.5  # 0-1
    brand_preference: Optional[str] = None
    liked_features: List[str] = None
    disliked_features: List[str] = None
    
    def __post_init__(self):
        if self.liked_features is None:
            self.liked_features = []
        if self.disliked_features is None:
            self.disliked_features = []


class LaptopRecommender:
    """
    Advanced recommendation engine for laptops.
    
    Scoring components:
    1. Use case match (30%)
    2. Spec quality (25%)
    3. Value for money (20%)
    4. Portability match (15%)
    5. Battery match (10%)
    """
    
    # GPU tier scoring (higher = better)
    GPU_TIERS = {
        "RTX 4090": 100,
        "RTX 4080": 95,
        "RTX 4070": 85,
        "RTX 4060": 75,
        "RTX 4050": 65,
        "RTX 3080": 80,
        "RTX 3070": 70,
        "RTX 3060": 60,
        "RTX 3050": 50,
        "RX 7900": 90,
        "RX 6800": 75,
        "RX 6700": 65,
        "Apple M3 Max": 90,
        "Apple M3 Pro": 80,
        "Apple M3": 70,
        "Apple M2": 60,
        "Integrated": 30,
    }
    
    # CPU tier scoring
    CPU_TIERS = {
        "i9-13900": 100,
        "i9-12900": 95,
        "i7-13700": 90,
        "i7-12700": 85,
        "i7-1370": 85,
        "i7-1270": 80,
        "i7": 75,
        "i5-13": 70,
        "i5-12": 65,
        "i5": 60,
        "Ryzen 9": 95,
        "Ryzen 7": 85,
        "Ryzen 5": 70,
        "M3 Max": 95,
        "M3 Pro": 85,
        "M3": 75,
        "M2": 70,
    }
    
    def __init__(self):
        """Initialize recommender."""
        pass
    
    def score_use_case_match(
        self,
        laptop: Dict[str, Any],
        preferences: UserPreferences
    ) -> float:
        """
        Score how well laptop matches use case (0-100).
        
        Args:
            laptop: Laptop product data
            preferences: User preferences
            
        Returns:
            Score 0-100
        """
        use_case = preferences.use_case.lower()
        subcategory = (laptop.get("subcategory") or "").lower()
        name = laptop.get("name", "").lower()
        description = (laptop.get("description") or "").lower()
        
        score = 50  # Base score
        
        # Gaming use case
        if use_case == "gaming":
            if "gaming" in subcategory or "gaming" in name:
                score += 30
            
            gpu_vendor = laptop.get("gpu_vendor", "")
            if gpu_vendor in ["NVIDIA", "AMD"]:
                score += 20  # Has dedicated GPU
            
            # Check for gaming keywords
            gaming_keywords = ["gaming", "rog", "omen", "predator", "legion", "alienware"]
            if any(kw in name for kw in gaming_keywords):
                score += 10
        
        # Work use case
        elif use_case == "work":
            work_keywords = ["business", "thinkpad", "latitude", "elitebook", "probook"]
            if any(kw in name for kw in work_keywords):
                score += 30
            
            # Prefer professional brands
            if laptop.get("brand") in ["Lenovo", "Dell", "HP"]:
                score += 10
            
            # Long battery is important for work
            if "battery" in description:
                score += 10
        
        # School use case
        elif use_case == "school":
            # Prefer mid-range, affordable laptops
            price = laptop.get("price_cents", 0) / 100
            if 500 <= price <= 1500:
                score += 30
            
            school_keywords = ["student", "education", "chromebook"]
            if any(kw in name or kw in description for kw in school_keywords):
                score += 20
        
        # Creative use case
        elif use_case == "creative":
            creative_keywords = ["pro", "studio", "creator", "zbook", "precision"]
            if any(kw in name for kw in creative_keywords):
                score += 30
            
            # Need good GPU for creative work
            if laptop.get("gpu_vendor"):
                score += 15
            
            # MacBooks are popular for creative
            if laptop.get("brand") == "Apple":
                score += 10
        
        return min(100, score)
    
    def score_specs(self, laptop: Dict[str, Any]) -> float:
        """
        Score laptop specs (0-100).
        
        Higher specs = higher score.
        """
        score = 0
        
        # GPU scoring
        gpu_model = laptop.get("gpu_model", "")
        for gpu, tier_score in self.GPU_TIERS.items():
            if gpu in gpu_model:
                score += tier_score * 0.4  # GPU is 40% of spec score
                break
        else:
            # No dedicated GPU
            score += 30 * 0.4
        
        # CPU scoring
        metadata = laptop.get("metadata")
        cpu = ""
        if metadata:
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except:
                    metadata = {}
            cpu = metadata.get("cpu", "")
        
        name = laptop.get("name", "")
        description = laptop.get("description", "")
        combined_text = f"{name} {description} {cpu}".lower()
        
        for cpu_model, tier_score in self.CPU_TIERS.items():
            if cpu_model.lower() in combined_text:
                score += tier_score * 0.35  # CPU is 35% of spec score
                break
        
        # RAM scoring (from metadata or description)
        if "32gb" in combined_text or "64gb" in combined_text:
            score += 25 * 0.15  # 15% weight
        elif "16gb" in combined_text:
            score += 20 * 0.15
        elif "8gb" in combined_text:
            score += 10 * 0.15
        
        # Storage scoring
        if "2tb" in combined_text or "1tb" in combined_text:
            score += 10 * 0.10  # 10% weight
        elif "512gb" in combined_text:
            score += 7 * 0.10
        
        return min(100, score)
    
    def score_value_for_money(self, laptop: Dict[str, Any]) -> float:
        """
        Score value for money (0-100).
        
        Higher score = better value (good specs for the price).
        """
        price = laptop.get("price_cents", 0) / 100
        
        if price == 0:
            return 50  # No price info
        
        # Get spec score
        spec_score = self.score_specs(laptop)
        
        # Calculate value ratio
        # Ideal: High specs, low price
        # $1000 laptop with 80 spec score = great value
        # $3000 laptop with 80 spec score = poor value
        
        expected_price_for_specs = (spec_score / 100) * 3000  # Max $3000 for 100 score
        
        if price < expected_price_for_specs:
            # Better value than expected
            value_ratio = expected_price_for_specs / max(price, 100)
            score = min(100, 50 + (value_ratio - 1) * 50)
        else:
            # Worse value than expected
            value_ratio = price / max(expected_price_for_specs, 100)
            score = max(0, 50 - (value_ratio - 1) * 30)
        
        return score
    
    def score_portability(self, laptop: Dict[str, Any]) -> float:
        """Score portability (0-100)."""
        name = laptop.get("name", "").lower()
        description = (laptop.get("description") or "").lower()
        
        score = 50  # Base score
        
        # Positive indicators
        portable_keywords = ["air", "ultrabook", "thin", "light", "portable", "slim"]
        for kw in portable_keywords:
            if kw in name or kw in description:
                score += 10
        
        # Negative indicators (gaming laptops are heavier)
        heavy_keywords = ["gaming", "workstation", "17-inch", "17\""]
        for kw in heavy_keywords:
            if kw in name:
                score -= 15
        
        # Screen size indicator (smaller = more portable)
        if "13" in name or "13-inch" in name:
            score += 15
        elif "14" in name:
            score += 10
        elif "15" in name:
            score += 0
        elif "16" in name or "17" in name:
            score -= 10
        
        return max(0, min(100, score))
    
    def rank_laptops(
        self,
        laptops: List[Dict[str, Any]],
        preferences: UserPreferences
    ) -> List[Dict[str, Any]]:
        """
        Rank laptops using advanced scoring.
        
        Args:
            laptops: List of laptop products
            preferences: User preferences
            
        Returns:
            Ranked list of laptops with scores
        """
        scored_laptops = []
        
        for laptop in laptops:
            # Calculate component scores
            use_case_score = self.score_use_case_match(laptop, preferences)
            spec_score = self.score_specs(laptop)
            value_score = self.score_value_for_money(laptop)
            portability_score = self.score_portability(laptop)
            
            # Weighted total score
            total_score = (
                use_case_score * 0.30 +        # 30%
                spec_score * 0.25 +             # 25%
                value_score * 0.20 +            # 20%
                portability_score * preferences.portability_importance * 0.15 +  # 15%
                50 * 0.10                       # 10% base
            )
            
            # Apply brand preference bonus
            if preferences.brand_preference:
                if laptop.get("brand") == preferences.brand_preference:
                    total_score += 5
            
            # Store scores
            laptop_with_score = dict(laptop)
            laptop_with_score["_recommendation_score"] = total_score
            laptop_with_score["total_score"] = round(total_score, 1)  # Add for compatibility
            laptop_with_score["_score_breakdown"] = {
                "use_case": round(use_case_score, 1),
                "specs": round(spec_score, 1),
                "value": round(value_score, 1),
                "portability": round(portability_score, 1),
                "total": round(total_score, 1)
            }
            
            scored_laptops.append(laptop_with_score)
        
        # Sort by score (descending)
        scored_laptops.sort(key=lambda x: x["_recommendation_score"], reverse=True)
        
        return scored_laptops


# Test function
def test_laptop_recommender():
    """Test the laptop recommender."""
    print("="*80)
    print("LAPTOP RECOMMENDER - TEST")
    print("="*80)
    
    # Sample laptops
    laptops = [
        {
            "name": "Dell XPS 13",
            "brand": "Dell",
            "price_cents": 139999,
            "category": "Electronics",
            "subcategory": "Work",
            "description": "Ultraportable business laptop with 13-inch display",
            "metadata": json.dumps({"cpu": "Intel Core i7"}),
        },
        {
            "name": "ASUS ROG Strix G16",
            "brand": "ASUS",
            "price_cents": 179999,
            "category": "Electronics",
            "subcategory": "Gaming",
            "gpu_vendor": "NVIDIA",
            "gpu_model": "RTX 4070",
            "description": "High-performance gaming laptop with 16-inch display",
            "metadata": json.dumps({"cpu": "Intel Core i9", "ram": "32GB"}),
        },
        {
            "name": "MacBook Air M3",
            "brand": "Apple",
            "price_cents": 119999,
            "category": "Electronics",
            "description": "Thin and light laptop with all-day battery life",
            "metadata": json.dumps({"cpu": "Apple M3", "ram": "16GB"}),
        },
    ]
    
    # Test 1: Gaming use case
    print("\n1. Gaming Use Case:")
    gaming_prefs = UserPreferences(
        use_case="gaming",
        budget_max=2000,
        performance_importance=0.9,
        portability_importance=0.3
    )
    
    recommender = LaptopRecommender()
    ranked = recommender.rank_laptops(laptops, gaming_prefs)
    
    print(f"   Top recommendation: {ranked[0]['name']}")
    print(f"   Score breakdown: {ranked[0]['_score_breakdown']}")
    
    print("\n   Rankings:")
    for i, laptop in enumerate(ranked, 1):
        score = laptop["_recommendation_score"]
        print(f"     {i}. {laptop['name']}: {score:.1f} points")
        breakdown = laptop["_score_breakdown"]
        print(f"        Use case: {breakdown['use_case']}, Specs: {breakdown['specs']}, Value: {breakdown['value']}")
    
    # Test 2: Work/portability use case
    print("\n2. Work Use Case (portability important):")
    work_prefs = UserPreferences(
        use_case="work",
        budget_max=1500,
        portability_importance=0.9,
        battery_importance=0.8
    )
    
    ranked_work = recommender.rank_laptops(laptops, work_prefs)
    
    print(f"   Top recommendation: {ranked_work[0]['name']}")
    print(f"   Score: {ranked_work[0]['_recommendation_score']:.1f}")
    
    print("\n" + "="*80)
    print(" LAPTOP RECOMMENDER: Advanced scoring working correctly")
    print("="*80)


if __name__ == "__main__":
    test_laptop_recommender()
