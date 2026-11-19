"""Run one measured SKY130A NAND2 switching netlist before the pilot."""
from __future__ import annotations
import json, random
from pathlib import Path
from dataset_generator import ROOT, load_config, widths_for, pick_sensitized_cycle
from gate_generator import build_gate, validate_boolean, truth, render_netlist
from graph_converter import save_graph
from spice_runner import preflight, run_netlist
from measurement_parser import checked_metrics

def main():
    cfg=load_config()
    model=__import__("os").environ.get("SKY130_MODEL_LIB") or cfg.get("model_lib","")
    pf=preflight(cfg["ngspice"],model)
    print(json.dumps(pf,indent=2))
    if not pf["ready"]:
        raise SystemExit("Smoke test blocked: ngspice and a loadable SKY130A tt model library are required.")
    rng=random.Random(int(cfg["seed"]))
    widths=widths_for("NAND2",rng,cfg)
    mos=build_gate("NAND2",widths,cfg["channel_length_um"])
    if not validate_boolean("NAND2",mos): raise SystemExit("NAND2 ideal-switch truth table failed.")
    scenario=pick_sensitized_cycle("NAND2",rng)
    if scenario is None: raise SystemExit("Could not construct a sensitized NAND2 cycle.")
    target,held,y_initial,y_mid=scenario
    work=ROOT/"data"/"smoke"/"spice"; work.mkdir(parents=True,exist_ok=True)
    wave="nand2_smoke_wave.dat"
    netlist=render_netlist("NAND2",mos,pf["model_library"],cfg["corner"],cfg["vdd_v"],cfg["load_f"],
        cfg["input_slew_s"],cfg["tran_step_s"],cfg["sim_time_s"],held,target,y_initial,wave,
        cfg["measure_start_s"],cfg["measure_end_s"])
    if not any(line.lower().startswith(".lib ") and line.rstrip().lower().endswith(" tt") for line in netlist.splitlines()):
        raise SystemExit("Generated NAND2 deck is missing the SKY130 tt corner include.")
    if not all(line.startswith("X") for line in netlist.splitlines() if line.startswith(("XM",))):
        raise SystemExit("Generated SKY130 devices must use X subcircuit instances.")
    result=run_netlist(netlist,work,cfg["ngspice"],"nand2_smoke")
    duration=cfg["measure_end_s"]-cfg["measure_start_s"]
    metrics=checked_metrics(result,cfg["vdd_v"],duration)
    m=result["measurements"]
    if (m["y_before"]>cfg["vdd_v"]/2)!=(y_initial==1): raise SystemExit("NAND2 initial output disagrees with Boolean truth table.")
    if (m["y_mid"]>cfg["vdd_v"]/2)!=(y_mid==1): raise SystemExit("NAND2 mid-cycle output disagrees with Boolean truth table.")
    if (m["y_after"]>cfg["vdd_v"]/2)!=(y_initial==1): raise SystemExit("NAND2 final output disagrees with Boolean truth table.")
    save_graph("NAND2",mos,ROOT/"data"/"smoke"/"nand2_smoke.json")
    report={"status":"success","gate":"NAND2","energy_j":metrics["energy_delivered_j"],"power_w":metrics["average_power_w"],
        "rise_delay_s":metrics["rise_delay_s"],"fall_delay_s":metrics["fall_delay_s"],"input_cycle":{"input":target,"held_inputs":held},
        "measurement_interval_s":{"start":cfg["measure_start_s"],"end":cfg["measure_end_s"],"duration":duration},"power_sign_reference":pf["power_sign_reference"],
        "netlist":str((work/"nand2_smoke.spice").relative_to(ROOT)),"log":str((work/"nand2_smoke.log").relative_to(ROOT)),
        "waveform":str((work/wave).relative_to(ROOT)),"model_preflight":pf}
    out=ROOT/"data"/"smoke"/"validation_report.json"; out.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))

if __name__=="__main__": main()
