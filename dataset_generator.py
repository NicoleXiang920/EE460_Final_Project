"""Reproducible pilot/full SKY130 SPICE dataset generation."""
from __future__ import annotations
import argparse, csv, json, random, hashlib
from pathlib import Path
import yaml
from gate_generator import GATES, build_gate, input_names, truth, validate_boolean, render_netlist
from graph_converter import save_graph
from spice_runner import preflight, run_netlist
from measurement_parser import checked_metrics

ROOT=Path(__file__).resolve().parent
def load_config(): return yaml.safe_load((ROOT/"config.yaml").read_text())
def widths_for(gate, rng, cfg):
    mos=build_gate(gate,{},cfg["channel_length_um"]) # topology gives exact device names/types
    lo=float(cfg["width_min_um"]); hi=float(cfg["width_max_um"]); grid=float(cfg["width_grid_um"])
    n=int(round((hi-lo)/grid)); return {m.name:round(lo+rng.randint(0,n)*grid,4) for m in mos}

def pick_sensitized_cycle(gate, rng):
    names=input_names(gate)
    candidates=[]
    for bit in names:
        for code in range(1<<len(names)):
            v={n:(code>>i)&1 for i,n in enumerate(names)}
            if v[bit] != 0: continue
            w=dict(v); w[bit]=1
            out0=truth(gate,v); out1=truth(gate,w)
            if out1!=out0: candidates.append((bit,v,out0,out1))
    return rng.choice(candidates) if candidates else None

def classify_failure(sim, validation_error=""):
    log_path=Path(sim.get("log","")) if sim.get("log") else None
    log=log_path.read_text(encoding="utf-8",errors="replace") if log_path and log_path.exists() else ""
    text=(validation_error+"\n"+sim.get("error","")+"\n"+log).lower()
    if any(s in text for s in ("can't find model","unknown subckt","could not open","no such file","model")):
        return "model_or_include_load"
    if any(s in text for s in ("timestep too small","no convergence","singular matrix","convergence")):
        return "convergence_or_timestep"
    if "tpd_rise" in text or "tpd_fall" in text or "trigger" in text or "target" in text:
        return "missing_50_percent_crossing_or_delay_measurement"
    if any(s in text for s in ("output did not cross","did not reach valid logic","contradicts sensitized")):
        return "output_not_switching_or_wrong_boolean_level"
    if any(s in text for s in ("energy","supply", "power")):
        return "power_energy_measurement_validation"
    if sim.get("status") != "success": return "ngspice_execution_or_measurement_failure"
    return "data_validation_failure"

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("mode",choices=["pilot","full"]); ap.add_argument("--model-lib",default="")
    ap.add_argument("--samples",type=int); ap.add_argument("--count",action="append",default=[],metavar="GATE=N",help="Override a gate's sample count; repeatable")
    ap.add_argument("--confirm-full",action="store_true")
    args=ap.parse_args(); cfg=load_config(); model=args.model_lib or __import__("os").environ.get("SKY130_MODEL_LIB") or cfg.get("model_lib","")
    pf=preflight(cfg["ngspice"],model); print(json.dumps(pf,indent=2))
    if not pf["ready"]:
        blocked=ROOT/"data"/args.mode; blocked.mkdir(parents=True,exist_ok=True)
        requested=args.samples or cfg["pilot_samples" if args.mode=="pilot" else "full_samples"]
        planned={g:requested//len(GATES)+(i<requested%len(GATES)) for i,g in enumerate(GATES)}
        report={"mode":args.mode,"requested_samples":requested,"written_rows":0,"successful":0,"failed":[],"by_gate":{g:{"requested":n,"valid":None,"failed":None,"status":"not_run"} for g,n in planned.items()},"preflight":pf,"status":"blocked_missing_dependencies","units":{"width":"um","length":"um","voltage":"V","capacitance":"F","power":"W","delay":"s","current":"A"},"note":"No circuit simulations were launched; valid/failed counts and measurement ranges are unavailable until ngspice and SKY130A models pass preflight."}
        (blocked/"validation_report.json").write_text(json.dumps(report,indent=2)+"\n")
        print("PILOT BLOCKED: install ngspice and set SKY130_MODEL_LIB to the ngspice-ready SKY130A sky130.lib.spice file.")
        raise SystemExit(2)
    if args.mode=="full" and not args.confirm_full: raise SystemExit("Full generation requires --confirm-full after reviewing pilot results.")
    total=args.samples or (cfg["pilot_samples"] if args.mode=="pilot" else cfg["full_samples"])
    counts=[total//len(GATES)+(i<total%len(GATES)) for i in range(len(GATES))]
    for override in args.count:
        try: gate,num=override.split("=",1); num=int(num)
        except Exception: raise SystemExit(f"Invalid --count {override!r}; use GATE=N")
        if gate not in GATES or num<0: raise SystemExit(f"Invalid gate/count in {override!r}")
        counts[GATES.index(gate)]=num
    total=sum(counts)
    if total<1: raise SystemExit("sample count must be >= 1")
    rng=random.Random(int(cfg["seed"])); base=ROOT/"data"/args.mode; base.mkdir(parents=True,exist_ok=True)
    rows=[]; failures=[]; used_width_configs={g:set() for g in GATES}
    sid=0
    for gate,count in zip(GATES,counts):
        for _ in range(count):
            sid+=1; sample=f"{args.mode}_{sid:05d}"; mos0=build_gate(gate,{},cfg["channel_length_um"])
            # Avoid accidentally reusing an identical sizing configuration for one topology.
            for _attempt in range(1000):
                widths=widths_for(gate,rng,cfg); signature=tuple(sorted(widths.items()))
                if signature not in used_width_configs[gate]:
                    used_width_configs[gate].add(signature); break
            else: raise RuntimeError(f"could not generate a new sizing configuration for {gate}")
            mos=build_gate(gate,widths,cfg["channel_length_um"])
            if not validate_boolean(gate,mos):
                failures.append({"sample_id":sample,"error":"ideal-switch Boolean validation failed"}); continue
            graph_path=base/"graphs"/f"{sample}.json"; save_graph(gate,mos,graph_path)
            scenarios={}; results={}
            scenario=pick_sensitized_cycle(gate,rng)
            if scenario is None:
                failures.append({"sample_id":sample,"error":"no functionally sensitized two-edge cycle found"}); continue
            target,vals,y0,y1=scenario
            net=render_netlist(gate,mos,pf["model_library"],cfg["corner"],cfg["vdd_v"],cfg["load_f"],cfg["input_slew_s"],cfg["tran_step_s"],cfg["sim_time_s"],vals,target,y0,sample+"_cycle_wave.dat",cfg["measure_start_s"],cfg["measure_end_s"])
            scenarios={"cycle":{"input":target,"initial_inputs":vals,"first_edge":"0->1","second_edge":"1->0","output_before":y0,"output_midcycle":y1,"output_after":y0}}
            results["cycle"]=run_netlist(net,base/"spice",cfg["ngspice"],sample+"_cycle")
            result_status="success" if results["cycle"]["status"]=="success" else "failed"
            netlist_paths={k:Path(v["log"]).with_suffix(".spice") for k,v in results.items()}
            netlist_hashes={k:(hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "") for k,p in netlist_paths.items()}
            row={"sample_id":sample,"topology_id":gate,"gate_type":gate,"transistor_count":len(mos),
                 "transistor_widths_um":json.dumps(widths,sort_keys=True),"transistor_types":json.dumps({m.name:m.kind for m in mos},sort_keys=True),
                 "circuit_connectivity":json.dumps([{k:getattr(m,k) for k in ("name","drain","gate","source","bulk")} for m in mos]),
                 "supply_voltage_v":cfg["vdd_v"],"load_capacitance_f":cfg["load_f"],
                 "input_transition":json.dumps(scenarios,sort_keys=True),
                 "energy_delivered_j":"","average_power_w":"","propagation_delay_s":"","rise_delay_s":"","fall_delay_s":"","average_supply_current_a":"",
                 "raw_measurements":json.dumps({k:v.get("measurements",{}) for k,v in results.items()},sort_keys=True),"failure_category":"",
                 "simulation_status":result_status,"graph_file":str(graph_path.relative_to(ROOT)),"netlist_file":json.dumps({k:str(p.relative_to(ROOT)) for k,p in netlist_paths.items()}),"netlist_sha256":json.dumps(netlist_hashes,sort_keys=True),"log_file":json.dumps({k:v["log"] for k,v in results.items()}),"waveform_file":json.dumps({k:str((base/"spice"/(sample+"_"+k+"_wave.dat")).relative_to(ROOT)) for k in results}),"measurement_interval_s":f"{cfg['measure_start_s']:.6g}..{cfg['measure_end_s']:.6g}"}
            try:
                sim=results["cycle"]
                duration=cfg["measure_end_s"]-cfg["measure_start_s"]
                metrics=checked_metrics(sim,cfg["vdd_v"],duration)
                m=sim["measurements"]
                expected=(y0,y1,y0)
                for key,value in zip(("y_before","y_mid","y_after"),expected):
                    measured_high=m[key]>cfg["vdd_v"]/2
                    if measured_high != bool(value): raise ValueError(f"{key} voltage contradicts sensitized Boolean expectation {value}")
                row["energy_delivered_j"]=metrics["energy_delivered_j"]
                row["average_power_w"]=metrics["average_power_w"]
                row["average_supply_current_a"]=metrics["average_supply_current_a"]
                row["rise_delay_s"]=metrics["rise_delay_s"]; row["fall_delay_s"]=metrics["fall_delay_s"]
                row["propagation_delay_s"]=metrics["propagation_delay_s"]
            except Exception as e:
                row["simulation_status"]="failed"
                row["failure_category"]=classify_failure(results["cycle"],str(e))
                failures.append({"sample_id":sample,"gate_type":gate,"error":str(e),"category":row["failure_category"],"logs":{k:v.get("log") for k,v in results.items()}})
            rows.append(row)
    out=base/"dataset.csv"
    if rows:
        with out.open("w",newline="") as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    by_gate={}
    for gate in GATES:
        subset=[r for r in rows if r["gate_type"]==gate]
        good=[r for r in subset if r["simulation_status"]=="success"]
        def numeric_range(field):
            vals=[float(r[field]) for r in good if r.get(field) not in (None,"")]
            return {"min":min(vals),"max":max(vals)} if vals else None
        by_gate[gate]={"requested":counts[GATES.index(gate)],"written":len(subset),"valid":len(good),"failed":len(subset)-len(good),
                       "energy_delivered_j_range":numeric_range("energy_delivered_j"),"average_power_w_range":numeric_range("average_power_w"),"propagation_delay_s_range":numeric_range("propagation_delay_s"),
                       "rise_delay_s_range":numeric_range("rise_delay_s"),"fall_delay_s_range":numeric_range("fall_delay_s")}
    report={"mode":args.mode,"requested_samples":total,"written_rows":len(rows),"successful":sum(r["simulation_status"]=="success" for r in rows),"failed":failures,"by_gate":by_gate,"preflight":pf,"boolean_validation":"Every generated circuit passed exhaustive ideal-switch truth-table checks before SPICE; each transient cycle is also checked against the sensitized Boolean states before, between, and after its two input edges.","units":{"width":"um","length":"um","voltage":"V","capacitance":"F","energy":"J","power":"W","delay":"s","current":"A"},"power_convention":"Integrate Pdelivered(t)=-V(VDD)*I(VDD) over one complete 2-edge input cycle, save net E_delivered in joules, and compute average_power_w=E_delivered/(measure_end_s-measure_start_s). Preflight checks current polarity and measured power against a 1.8 V / 1 kohm reference.","measurement_interval_s":{"start":cfg["measure_start_s"],"end":cfg["measure_end_s"],"duration":cfg["measure_end_s"]-cfg["measure_start_s"]},"note":"Failed samples retain raw measurements and diagnostic logs; labels are emitted only after energy, delay, both 50% crossings, and Boolean states validate."}
    (base/"validation_report.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2)); print(f"Dataset: {out}")
    if report["failed"]: raise SystemExit(1)
if __name__=="__main__": main()
