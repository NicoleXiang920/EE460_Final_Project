"""Summarize dataset integrity and write a human-readable validation report."""
import argparse,csv,json
from pathlib import Path

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("dataset",nargs="?",default="data/pilot/dataset.csv"); a=ap.parse_args()
    p=Path(a.dataset); rows=list(csv.DictReader(p.open())) if p.exists() else []
    good=[r for r in rows if r.get("simulation_status")=="success"]
    failures=[r["sample_id"] for r in rows if r.get("simulation_status")!="success"]
    report={"dataset":str(p),"rows":len(rows),"successful":len(good),"failed_sample_ids":failures,
            "missing_power":sum(not r.get("average_power_w") for r in rows),"missing_delay":sum(not r.get("propagation_delay_s") for r in rows),
            "gate_counts":{g:sum(r.get("gate_type")==g for r in rows) for g in sorted({r.get("gate_type") for r in rows})}}
    out=p.with_name("validation_report.json"); out.write_text(json.dumps(report,indent=2)+"\n"); print(json.dumps(report,indent=2))
if __name__=="__main__": main()
