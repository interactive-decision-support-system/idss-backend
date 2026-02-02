#!/usr/bin/env python3
"""
Interactive demo for the Simplified IDSS.

Usage:
    python scripts/demo.py              # Default: k=3 (3 questions)
    python scripts/demo.py --k 0        # Direct recommendations (no questions)
    python scripts/demo.py --k 5        # Ask up to 5 questions
"""
import argparse
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from idss import create_controller, IDSSResponse


def format_vehicle(v: dict, idx: int) -> str:
    """Format a vehicle for display."""
    vehicle = v.get('vehicle', {})
    retail = v.get('retailListing', {})

    year = vehicle.get('year', '?')
    make = vehicle.get('make', '?')
    model = vehicle.get('model', '?')
    price = retail.get('price', vehicle.get('price', 0))
    mileage = retail.get('miles', vehicle.get('mileage', 0))

    price_str = f"${price:,}" if price else "Price N/A"
    mileage_str = f"{mileage:,} mi" if mileage else "Mileage N/A"

    return f"  {idx}. {year} {make} {model} - {price_str} ({mileage_str})"


def display_recommendations(response: IDSSResponse) -> None:
    """Display recommendations in a formatted way."""
    print("\n" + "=" * 60)
    print("RECOMMENDATIONS")
    print("=" * 60)
    print(f"\n{response.message}\n")

    if response.diversification_dimension:
        print(f"Diversified by: {response.diversification_dimension}")
        print("-" * 40)

    if response.recommendations:
        for row_idx, (bucket, label) in enumerate(zip(response.recommendations, response.bucket_labels or [])):
            print(f"\n[{label}]")
            if bucket:
                for i, vehicle in enumerate(bucket, 1):
                    print(format_vehicle(vehicle, i))
            else:
                print("  (No vehicles in this bucket)")

    total = sum(len(b) for b in (response.recommendations or []))
    print(f"\nTotal: {total} vehicles")
    print("=" * 60)


def display_question(response: IDSSResponse) -> None:
    """Display a question with quick replies."""
    print("\n" + "-" * 40)
    print(f"Q: {response.message}")

    if response.quick_replies:
        print("\nQuick replies:")
        for i, reply in enumerate(response.quick_replies, 1):
            print(f"  [{i}] {reply}")


def main():
    parser = argparse.ArgumentParser(description='Interactive IDSS Demo')
    parser.add_argument('--k', type=int, default=3,
                        help='Number of questions to ask (0 for direct recommendations)')
    parser.add_argument('--n-per-row', type=int, default=3,
                        help='Vehicles per row in output')
    parser.add_argument('--method', type=str, default='embedding_similarity', choices=['embedding_similarity', 'coverage_risk'],
                        help='Recommendation method to use')
    args = parser.parse_args()

    print("=" * 60)
    print("SIMPLIFIED IDSS - Interactive Demo")
    print("=" * 60)
    print(f"Configuration: k={args.k} (questions), n_per_row={args.n_per_row}, method={args.method}")
    print("Type 'quit' to exit, 'reset' to start over")
    print("=" * 60)

    # Create controller
    controller = create_controller(
        k=args.k,
        n_vehicles_per_row=args.n_per_row,
        recommendation_method=args.method
    )

    while True:
        try:
            # Get user input
            user_input = input("\nYou: ").strip()

            if not user_input:
                continue

            if user_input.lower() == 'quit':
                print("Goodbye!")
                break

            if user_input.lower() == 'reset':
                controller.reset_session()
                print("Session reset. Start a new conversation!")
                continue

            # Process input
            response = controller.process_input(user_input)

            # Display response based on type
            if response.response_type == 'question':
                display_question(response)
            else:
                display_recommendations(response)
                print("\nType 'reset' to start a new search, or continue the conversation.")

        except KeyboardInterrupt:
            print("\n\nGoodbye!")
            break
        except Exception as e:
            print(f"\nError: {e}")
            import traceback
            traceback.print_exc()


if __name__ == '__main__':
    main()
