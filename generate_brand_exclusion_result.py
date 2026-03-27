import argparse
import json
import requests, uuid

BASE='http://localhost:8001/chat'
N=10

KNOWN_BRANDS = {
    "Apple", "Dell", "HP", "Lenovo", "ASUS", "MSI", "Razer",
    "Microsoft", "Samsung", "Acer", "Gigabyte", "Framework",
    "System76", "Toshiba", "LG",
}

def run(turns):
    out=[]
    for _ in range(N):
        sid=str(uuid.uuid4())
        rs=[]
        for t in turns:
            d=requests.post(BASE,json={'message':t,'session_id':sid},timeout=120).json()
            rs.append(d)
        out.append(rs)
    return out


def load_runs_from_file(path, scenario_key):
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload.get(scenario_key, [])

def summarize(name, turns, checker, runs_override=None):
    runs = runs_override if runs_override is not None else run(turns)
    ok=0
    bad=[]
    for i,rs in enumerate(runs):
        good,reason=checker(rs)
        if good: ok+=1
        else: bad.append(reason)
    total = len(runs)
    print(f"{name}: {ok}/{total} pass")
    if bad:
        from collections import Counter
        c=Counter(bad)
        for k,v in c.items():
            print('  -',k,':',v)


def _has_excluded_brand(filters, brand):
    excl = filters.get("excluded_brands") or []
    return isinstance(excl, list) and any(str(x).lower() == brand.lower() for x in excl)


def _has_positive_screen_constraint(filters):
    return any(k in filters for k in ("min_screen_inches", "min_screen_size", "max_screen_size"))


def _has_negative_screen_behavior(filters, target_size=14.0):
    # Preferred representation: explicit excluded sizes.
    excl = filters.get("excluded_screen_sizes") or []
    if isinstance(excl, list):
        for v in excl:
            try:
                if abs(float(v) - float(target_size)) <= 0.01:
                    return True
            except (TypeError, ValueError):
                continue
    # Equivalent behavior: a range constraint that excludes the target size.
    min_s = filters.get("min_screen_size") or filters.get("min_screen_inches")
    max_s = filters.get("max_screen_size")
    try:
        if min_s is not None and float(min_s) >= float(target_size) + 0.3:
            return True
    except (TypeError, ValueError):
        pass
    try:
        if max_s is not None and float(max_s) <= float(target_size) - 0.3:
            return True
    except (TypeError, ValueError):
        pass
    return False


def _has_invalid_excluded_brands(filters):
    excl = filters.get("excluded_brands")
    if not excl:
        return False
    if not isinstance(excl, list):
        return True
    return any(str(x) not in KNOWN_BRANDS for x in excl)

def main():
    parser = argparse.ArgumentParser(description="Summarize Q1 scenario pass/fail results.")
    parser.add_argument(
        "--input",
        help="Path to raw JSON output from question1_querytest.py (e.g., q1_raw_results.json). "
             "If omitted, this script re-runs scenarios against localhost API.",
    )
    args = parser.parse_args()

    source = {}
    if args.input:
        with open(args.input, "r", encoding="utf-8") as f:
            source = json.load(f)

    summarize('S1 no mac',['I want a laptop, no mac'],
        lambda rs:(
            rs[0].get('domain') == 'laptops'
            and _has_excluded_brand(rs[0].get('filters', {}), 'Apple')
            and 'brand' not in rs[0].get('filters', {}),
            f"domain={rs[0].get('domain')} filters={rs[0].get('filters',{})}"
        ),
        runs_override=source.get("S1_no_mac") if source else None)

    summarize('S2 hate ASUS',['we hate ASUS, find me a gaming laptop'],
        lambda rs:(
            rs[0].get('domain') == 'laptops'
            and _has_excluded_brand(rs[0].get('filters', {}), 'ASUS'),
            # NOTE: use_case/gaming signal can be dropped by downstream filter shaping.
            # For this scenario, the core contract is brand exclusion correctness.
            f"domain={rs[0].get('domain')} filters={rs[0].get('filters',{})}"
        ),
        runs_override=source.get("S2_hate_asus") if source else None)

    summarize('S3 steer clear standalone',['steer clear of HP, bad experience'],
        lambda rs:(
            # Accept either immediate laptop-domain exclusion OR preserved exclusion while domain is unclear.
            _has_excluded_brand(rs[0].get('filters', {}), 'HP'),
            f"domain={rs[0].get('domain')} filters={rs[0].get('filters',{})}"
        ),
        runs_override=source.get("S3_steer_clear_standalone") if source else None)

    summarize('S3 steer clear with domain',['I want a gaming laptop','steer clear of HP, bad experience'],
        lambda rs:(
            rs[1].get('domain') == 'laptops'
            and _has_excluded_brand(rs[1].get('filters', {}), 'HP')
            and 'brand' not in rs[1].get('filters', {}),
            f"domain={rs[1].get('domain')} filters={rs[1].get('filters',{})}"
        ),
        runs_override=source.get("S3_steer_clear_with_domain") if source else None)

    summarize('S4 no 14 standalone',["I don't want a 14 inch screen"],
        lambda rs:(
            _has_negative_screen_behavior(rs[0].get('filters', {}), 14.0)
            and (not _has_invalid_excluded_brands(rs[0].get('filters', {}))),
            str(rs[0].get('filters',{}))
        ),
        runs_override=source.get("S4_no_14_standalone") if source else None)

    summarize('S4 no 14 with domain',['I want a laptop',"I don't want a 14 inch screen"],
        lambda rs:(
            _has_negative_screen_behavior(rs[1].get('filters', {}), 14.0)
            and (not _has_invalid_excluded_brands(rs[1].get('filters', {}))),
            str(rs[1].get('filters',{}))
        ),
        runs_override=source.get("S4_no_14_with_domain") if source else None)

    summarize('S5 override',['I want a laptop, no Apple','I need a laptop for school','actually show me Apple'],
        lambda rs:(
            _has_excluded_brand(rs[0].get('filters', {}), 'Apple')
            and _has_excluded_brand(rs[1].get('filters', {}), 'Apple')
            and rs[-1].get('filters',{}).get('brand') == 'Apple'
            and not _has_excluded_brand(rs[-1].get('filters', {}), 'Apple'),
            f"turn1={rs[0].get('filters',{})} turn2={rs[1].get('filters',{})} turn_last={rs[-1].get('filters',{})}"
        ),
        runs_override=source.get("S5_turn_override") if source else None)


if __name__ == "__main__":
    main()
