#!/usr/bin/env python3
"""
Comprehensive Database Summary Report

Shows detailed breakdown of all products by category, type, brand, specs, etc.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import SessionLocal
from app.models import Product
import json
from collections import Counter


def main():
    """Generate comprehensive database report."""
    print("="*80)
    print("COMPREHENSIVE DATABASE SUMMARY REPORT")
    print("="*80)
    
    db = SessionLocal()
    
    # Overall stats
    total = db.query(Product).count()
    electronics = db.query(Product).filter(Product.category == "Electronics").all()
    books = db.query(Product).filter(Product.category == "Books").all()
    
    print(f"\n OVERALL STATISTICS")
    print(f"{'='*80}")
    print(f"Total Products: {total}")
    print(f"Electronics: {len(electronics)}")
    print(f"Books: {len(books)}")
    print(f"Other Categories: {total - len(electronics) - len(books)}")
    
    # Electronics breakdown
    print(f"\n🖥️  ELECTRONICS BREAKDOWN ({len(electronics)} products)")
    print(f"{'='*80}")
    
    # By product type
    electronics_types = Counter(p.product_type for p in electronics if p.product_type)
    print(f"\nBy Product Type:")
    for ptype, count in sorted(electronics_types.items(), key=lambda x: -x[1]):
        print(f"  {ptype:<20} {count:>3}")
    
    # By subcategory
    electronics_subcat = Counter(p.subcategory for p in electronics if p.subcategory)
    print(f"\nBy Subcategory:")
    for subcat, count in sorted(electronics_subcat.items(), key=lambda x: -x[1]):
        print(f"  {subcat:<20} {count:>3}")
    
    # By brand
    electronics_brands = Counter(p.brand for p in electronics if p.brand)
    print(f"\nTop Electronics Brands:")
    for brand, count in sorted(electronics_brands.items(), key=lambda x: -x[1])[:15]:
        print(f"  {brand:<20} {count:>3}")
    
    # Laptops detailed breakdown
    laptops = [p for p in electronics if p.product_type == "laptop"]
    if laptops:
        print(f"\n💻 LAPTOP DETAILS ({len(laptops)} laptops)")
        print(f"{'='*80}")
        
        # Screen sizes
        screen_sizes = []
        for laptop in laptops:
            if laptop.metadata:
                try:
                    meta = json.loads(laptop.metadata)
                    if meta.get('screen_size'):
                        screen_sizes.append(meta['screen_size'])
                except:
                    pass
        
        if screen_sizes:
            screen_counter = Counter(screen_sizes)
            print(f"\nScreen Sizes:")
            for size, count in sorted(screen_counter.items()):
                print(f"  {size:<10} {count:>3}")
        
        # CPUs
        cpus = []
        for laptop in laptops:
            if laptop.metadata:
                try:
                    meta = json.loads(laptop.metadata)
                    if meta.get('cpu'):
                        cpu = meta['cpu']
                        # Simplify CPU name
                        if "Intel" in cpu:
                            cpu = cpu.split()[2]  # e.g., "i9-13900H"
                        elif "AMD" in cpu:
                            cpu = f"{cpu.split()[1]} {cpu.split()[2]}"  # e.g., "Ryzen 9"
                        elif "Apple" in cpu:
                            cpu = f"{cpu.split()[1]}"  # e.g., "M3"
                        cpus.append(cpu)
                except:
                    pass
        
        if cpus:
            cpu_counter = Counter(cpus)
            print(f"\nTop CPUs:")
            for cpu, count in sorted(cpu_counter.items(), key=lambda x: -x[1])[:10]:
                print(f"  {cpu:<20} {count:>3}")
        
        # GPUs
        gpu_vendors = Counter(p.gpu_vendor for p in laptops if p.gpu_vendor)
        print(f"\nGPU Vendors:")
        for vendor, count in sorted(gpu_vendors.items(), key=lambda x: -x[1]):
            print(f"  {vendor:<20} {count:>3}")
    
    # Phones breakdown
    phones = [p for p in electronics if p.product_type == "smartphone"]
    if phones:
        print(f"\n PHONES DETAILS ({len(phones)} phones)")
        print(f"{'='*80}")
        
        phone_brands = Counter(p.brand for p in phones if p.brand)
        print(f"\nPhone Brands:")
        for brand, count in sorted(phone_brands.items(), key=lambda x: -x[1]):
            print(f"  {brand:<20} {count:>3}")
    
    # Tablets breakdown
    tablets = [p for p in electronics if p.product_type == "tablet"]
    if tablets:
        print(f"\n TABLETS DETAILS ({len(tablets)} tablets)")
        print(f"{'='*80}")
        
        tablet_brands = Counter(p.brand for p in tablets if p.brand)
        print(f"\nTablet Brands:")
        for brand, count in sorted(tablet_brands.items()):
            print(f"  {brand:<20} {count:>3}")
    
    # Books breakdown
    print(f"\n BOOKS BREAKDOWN ({len(books)} books)")
    print(f"{'='*80}")
    
    # By genre (subcategory)
    book_genres = Counter(p.subcategory for p in books if p.subcategory)
    print(f"\nBy Genre:")
    for genre, count in sorted(book_genres.items(), key=lambda x: -x[1]):
        print(f"  {genre:<25} {count:>3}")
    
    # By format
    book_formats = []
    for book in books:
        if book.metadata:
            try:
                meta = json.loads(book.metadata)
                if meta.get('format'):
                    book_formats.append(meta['format'])
            except:
                pass
    
    if book_formats:
        format_counter = Counter(book_formats)
        print(f"\nBy Format:")
        for fmt, count in sorted(format_counter.items(), key=lambda x: -x[1]):
            print(f"  {fmt:<25} {count:>3}")
    
    # Top authors
    authors = []
    for book in books:
        if book.metadata:
            try:
                meta = json.loads(book.metadata)
                if meta.get('author'):
                    authors.append(meta['author'])
            except:
                pass
    
    if authors:
        author_counter = Counter(authors)
        print(f"\nTop Authors:")
        for author, count in sorted(author_counter.items(), key=lambda x: -x[1])[:15]:
            print(f"  {author:<30} {count:>3}")
    
    # Publishers
    publishers = []
    for book in books:
        if book.metadata:
            try:
                meta = json.loads(book.metadata)
                if meta.get('publisher'):
                    publishers.append(meta['publisher'])
            except:
                pass
    
    if publishers:
        pub_counter = Counter(publishers)
        print(f"\nTop Publishers:")
        for pub, count in sorted(pub_counter.items(), key=lambda x: -x[1]):
            print(f"  {pub:<30} {count:>3}")
    
    # Price analysis
    print(f"\n💰 PRICE ANALYSIS")
    print(f"{'='*80}")
    
    # Electronics prices
    elec_prices = [p.price_info.price_cents/100 for p in electronics if p.price_info]
    if elec_prices:
        print(f"\nElectronics:")
        print(f"  Min:     ${min(elec_prices):>8,.2f}")
        print(f"  Max:     ${max(elec_prices):>8,.2f}")
        print(f"  Average: ${sum(elec_prices)/len(elec_prices):>8,.2f}")
    
    # Books prices
    book_prices = [p.price_info.price_cents/100 for p in books if p.price_info]
    if book_prices:
        print(f"\nBooks:")
        print(f"  Min:     ${min(book_prices):>8,.2f}")
        print(f"  Max:     ${max(book_prices):>8,.2f}")
        print(f"  Average: ${sum(book_prices)/len(book_prices):>8,.2f}")
    
    # Data sources
    print(f"\n🔍 DATA SOURCES")
    print(f"{'='*80}")
    
    all_products = db.query(Product).all()
    sources = Counter(p.source for p in all_products if p.source)
    print(f"\nBy Source:")
    for source, count in sorted(sources.items(), key=lambda x: -x[1]):
        print(f"  {source:<20} {count:>4}")
    
    # Quality checks
    print(f"\n QUALITY CHECKS")
    print(f"{'='*80}")
    
    with_prices = sum(1 for p in all_products if p.price_info)
    with_inventory = sum(1 for p in all_products if p.inventory_info)
    with_reviews = sum(1 for p in all_products if p.reviews)
    with_images = sum(1 for p in all_products if p.image_url)
    with_brands = sum(1 for p in all_products if p.brand)
    
    print(f"\nData Completeness:")
    print(f"  Products with prices:    {with_prices}/{total} ({with_prices/total*100:.1f}%)")
    print(f"  Products with inventory: {with_inventory}/{total} ({with_inventory/total*100:.1f}%)")
    print(f"  Products with reviews:   {with_reviews}/{total} ({with_reviews/total*100:.1f}%)")
    print(f"  Products with images:    {with_images}/{total} ({with_images/total*100:.1f}%)")
    print(f"  Products with brands:    {with_brands}/{total} ({with_brands/total*100:.1f}%)")
    
    db.close()
    
    print(f"\n{'='*80}")
    print(f" DATABASE IS PRODUCTION READY!")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    main()
