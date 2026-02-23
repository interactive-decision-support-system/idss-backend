import json
from statistics import mean, stdev
from pathlib import Path

def parse_logs(logfile):
    timings = []
    with open(logfile) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue  # skip non-JSON lines (uvicorn startup, access logs, etc.)
            if 'backendTimings' in entry or 'timings_ms' in entry:
                t = entry.get('backendTimings') or entry.get('timings_ms')
                timings.append({
                    'session_id': entry.get('session_id'),
                    'llm_question_ms': t.get('question_generation_ms'),
                    'llm_criteria_ms': t.get('criteria_extraction_ms'),
                    'llm_domain_ms': t.get('domain_detection_ms'),
                    'backend_total_ms': t.get('total_backend_ms'),
                    'search_ms': t.get('vehicle_search_ms') or t.get('ecommerce_search_ms'),
                    'format_ms': t.get('vehicle_formatting_ms') or t.get('ecommerce_formatting_ms'),
                    'frontend_total_ms': entry.get('totalToRender'),
                    'frontend_api_to_render_ms': entry.get('apiToRender'),
                })
    return timings

def summarize(timings, key):
    vals = [t[key] for t in timings if t[key] is not None]
    if not vals:
        return None
    return {
        'min': min(vals),
        'max': max(vals),
        'mean': mean(vals),
        'stdev': stdev(vals) if len(vals) > 1 else 0,
    }

def print_summary_table(timings):
    metrics = [
        ('llm_domain_ms', 'LLM domain detection'),
        ('llm_criteria_ms', 'LLM criteria extraction'),
        ('llm_question_ms', 'LLM question generation'),
        ('search_ms', 'Product search (Redis/KG)'),
        ('format_ms', 'Product formatting'),
        ('backend_total_ms', 'Backend total'),
        ('frontend_api_to_render_ms', 'Frontend: API→render'),
        ('frontend_total_ms', 'End-to-end (user→screen)'),
    ]
    print("| Step | Min (ms) | Max (ms) | Mean (ms) | Stddev (ms) |")
    print("|-----------------------------|----------|----------|-----------|-------------|")
    for key, label in metrics:
        stats = summarize(timings, key)
        if stats:
            print(f"| {label:27} | {stats['min']:.1f} | {stats['max']:.1f} | {stats['mean']:.1f} | {stats['stdev']:.1f} |")
        else:
            print(f"| {label:27} |   -    |   -    |   -    |   -    |")

if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage: python parse_latency_logs.py <logfile.jsonl>")
        exit(1)
    logs = parse_logs(sys.argv[1])
    print_summary_table(logs)
