"""Persist role-preserving MOS graph records as JSON."""
import json
from pathlib import Path
from gate_generator import graph_record

def save_graph(gate, mos, path: Path):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(graph_record(gate,mos),indent=2)+"\n")
