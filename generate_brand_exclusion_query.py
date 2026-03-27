import argparse
import json
import requests
import uuid

BASE = 'http://localhost:8001/chat'

SCENARIOS = [
    ('S1_no_mac', ['I want a laptop, no mac']),

    ('S2_hate_asus', ['we hate ASUS, find me a gaming laptop']),

    ('S3_steer_clear_standalone', ['steer clear of HP, bad experience']),
    ('S3_steer_clear_with_domain', ['I want a gaming laptop', 'steer clear of HP, bad experience']),

    ('S4_no_14_standalone', ["I don't want a 14 inch screen"]),
    ('S4_no_14_with_domain', ['I want a laptop', "I don't want a 14 inch screen"]),

    ('S5_turn_override', ['I want a laptop, no Apple', 'I need a laptop for school', 'actually show me Apple']),
]


def post(msg, sid):
    r = requests.post(BASE, json={'message': msg, 'session_id': sid}, timeout=120)
    r.raise_for_status()
    return r.json()


def run_scenarios(n_runs: int, shared_session_across_scenarios: bool):
    all_results = {name: [] for name, _ in SCENARIOS}

    if shared_session_across_scenarios:
        # One persistent session per run index; all scenarios for that run share it.
        for run_idx in range(n_runs):
            sid = str(uuid.uuid4())
            for name, turns in SCENARIOS:
                run = []
                for t in turns:
                    d = post(t, sid)
                    recs = d.get('recommendations') or []
                    brands = [p.get('brand') for row in recs for p in row if p.get('brand')]
                    names = [(p.get('name') or '') for row in recs for p in row]
                    run.append({
                        'session_id': sid,
                        'msg': t,
                        'type': d.get('response_type'),
                        'domain': d.get('domain'),
                        'filters': d.get('filters', {}),
                        'message': d.get('message', ''),
                        'brands': brands[:12],
                        'names': names[:12],
                    })
                all_results[name].append(run)
    else:
        # Independent session per scenario-run (original behavior).
        for name, turns in SCENARIOS:
            runs = []
            for _ in range(n_runs):
                sid = str(uuid.uuid4())
                run = []
                for t in turns:
                    d = post(t, sid)
                    recs = d.get('recommendations') or []
                    brands = [p.get('brand') for row in recs for p in row if p.get('brand')]
                    names = [(p.get('name') or '') for row in recs for p in row]
                    run.append({
                        'session_id': sid,
                        'msg': t,
                        'type': d.get('response_type'),
                        'domain': d.get('domain'),
                        'filters': d.get('filters', {}),
                        'message': d.get('message', ''),
                        'brands': brands[:12],
                        'names': names[:12],
                    })
                runs.append(run)
            all_results[name] = runs

    all_results["_meta"] = {
        "n_runs": n_runs,
        "shared_session_across_scenarios": shared_session_across_scenarios,
    }
    return all_results


def main():
    parser = argparse.ArgumentParser(description="Run Q1 scenario turns and emit raw JSON results.")
    parser.add_argument("--n", type=int, default=10, help="Number of runs per scenario (default: 10)")
    parser.add_argument(
        "--shared-session-across-scenarios",
        action="store_true",
        help="Use one session per run index across all scenarios (state carries between scenarios).",
    )
    args = parser.parse_args()

    payload = run_scenarios(
        n_runs=args.n,
        shared_session_across_scenarios=args.shared_session_across_scenarios,
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
