"""ngspice batch execution and measurement parsing."""
from __future__ import annotations
import os, re, shutil, subprocess, ntpath, json
from pathlib import Path
from typing import Optional

MEAS = re.compile(r"^\s*(pavg|tpd|tpd_rise|tpd_fall|y_initial|y_final|y_before|y_mid|y_after|ivdd_avg|vdd_avg|e_delivered|edel)\s*=\s*([-+0-9.eE]+)", re.I|re.M)

def _power_sign_reference(exe: Optional[str], diagnostic_dir: Path) -> dict:
    """Check voltage-source current polarity and delivered-energy sign with R=1 kohm."""
    deck_path=diagnostic_dir/"power_sign_reference.spice"
    log_path=diagnostic_dir/"power_sign_reference.log"
    deck='''* Power sign reference: 1.8 V source delivering to 1 kohm
VDD vdd 0 1.8
RLOAD vdd 0 1k
.control
tran 2p 2n
meas tran ivdd_avg AVG i(VDD) from=0.5n to=1.5n
meas tran vdd_avg AVG v(vdd) from=0.5n to=1.5n
let pdel = -v(vdd)*i(VDD)
meas tran pavg AVG pdel from=0.5n to=1.5n
meas tran edel INTEG pdel from=0.5n to=1.5n
quit
.endc
.end
'''
    deck_path.write_text(deck,encoding="utf-8")
    if not exe:
        message="ngspice executable unavailable; reference circuit was not simulated"
        log_path.write_text(message+"\n",encoding="utf-8")
        return {"status":"blocked","deck":str(deck_path),"log":str(log_path),"measurements":{},"error":message}
    try:
        proc=subprocess.run([exe,"-b",str(deck_path)],cwd=diagnostic_dir,text=True,encoding="utf-8",errors="replace",
                            stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=60)
        output=proc.stdout or ""; log_path.write_text(output,encoding="utf-8")
        measured={k.lower():float(v) for k,v in MEAS.findall(output)}
        expected=1.8**2/1000
        ok=(proc.returncode==0 and not re.search(r"(?:^|\n)\s*(?:Error:|Error on line|fatal error)",output,re.I)
            and {"ivdd_avg","vdd_avg","pavg","edel"}<=measured.keys()
            and measured["ivdd_avg"]<0 and abs(measured["vdd_avg"]-1.8)<0.018
            and abs(measured["pavg"]-expected)<expected*.01
            and abs(measured["edel"]-expected*1e-9)<expected*1e-11)
        return {"status":"pass" if ok else "fail","deck":str(deck_path),"log":str(log_path),
                "measurements":measured,"expected_power_w":expected,"expected_energy_j":expected*1e-9,
                "error":None if ok else "reference measurements did not match a 1.8 V source delivering into 1 kohm"}
    except Exception as exc:
        message=f"{type(exc).__name__}: {exc}"; log_path.write_text(message+"\n",encoding="utf-8")
        return {"status":"fail","deck":str(deck_path),"log":str(log_path),"measurements":{},"error":message}

def resolve_batch_executable(executable: str) -> Optional[str]:
    """Find ngspice; Windows batch jobs must use the console executable."""
    raw=os.path.expanduser(str(executable).strip().strip('"'))
    is_windows=os.name=="nt"
    if is_windows:
        win_path=Path(raw)
        name=win_path.name.lower()
        if name=="ngspice.exe":
            win_path=win_path.with_name("ngspice_con.exe")
        elif name=="ngspice":
            win_path=win_path.with_name("ngspice_con.exe")
        raw=str(win_path)
    candidate=Path(raw)
    # ntpath.isabs recognizes C:/... even when this module is inspected on POSIX.
    if candidate.is_absolute() or ntpath.isabs(raw):
        return str(candidate) if candidate.is_file() else None
    return shutil.which(raw)

def _ensure_spiceinit(workdir: Path) -> Path:
    """Set the SKY130 parser modes locally so batch jobs do not depend on caller cwd."""
    init=workdir/".spiceinit"
    existing=init.read_text(encoding="utf-8",errors="replace") if init.exists() else ""
    additions=[]
    if not re.search(r"^\s*set\s+ngbehavior\s*=\s*hsa\b",existing,re.I|re.M):
        additions.append("set ngbehavior=hsa")
    if not re.search(r"^\s*set\s+skywaterpdk\b",existing,re.I|re.M):
        additions.append("set skywaterpdk")
    if additions: init.write_text(existing.rstrip()+("\n" if existing.strip() else "")+"\n".join(additions)+"\n",encoding="utf-8")
    return init

def trace_model_library(model_lib: str) -> dict:
    """Walk the selected TT include tree and summarize MOS wrappers and model bins."""
    root=Path(model_lib).expanduser().resolve()
    include_re=re.compile(r"^\s*\.include\s+(?:\"([^\"]+)\"|'([^']+)'|(\S+))",re.I)
    section_re=re.compile(r"^\s*\.lib\s+(\S+)(?:\s+.*)?$",re.I)
    end_re=re.compile(r"^\s*\.endl(?:\s+\S+)?",re.I)
    subckt_re=re.compile(r"^\s*\.subckt\s+(sky130_fd_pr__(?:nfet|pfet)_01v8)\b",re.I)
    model_re=re.compile(r"^\s*\.model\s+(sky130_fd_pr__(?:nfet|pfet)_01v8__model(?:\.\d+)?)\s+(nmos|pmos)\b",re.I)
    seen=set(); edges=[]; missing=[]; subckts={}; bins={"nfet":[],"pfet":[]}

    def visit(path: Path, chain: list[str], top=False):
        path=path.resolve()
        if path in seen or not path.is_file(): return
        seen.add(path)
        try: lines=path.read_text(encoding="utf-8",errors="replace").splitlines()
        except OSError as exc:
            missing.append({"path":str(path),"error":str(exc),"chain":chain+[str(path)]}); return
        in_tt=not top
        for number,line in enumerate(lines,1):
            stripped=line.strip()
            if not stripped or stripped.startswith("*"): continue
            if top:
                section=section_re.match(line)
                if section:
                    in_tt=section.group(1).lower()=="tt"
                    continue
                if end_re.match(line): in_tt=False; continue
            if not in_tt: continue
            sm=subckt_re.match(line)
            if sm: subckts[sm.group(1).lower()]=str(path)
            mm=model_re.match(line)
            if mm:
                typ="nfet" if "nfet" in mm.group(1).lower() else "pfet"
                bins[typ].append({"name":mm.group(1),"type":mm.group(2).lower(),"file":str(path),"line":number})
            im=include_re.match(line)
            if not im: continue
            ref=next(value for value in im.groups() if value is not None)
            ref=os.path.expandvars(os.path.expanduser(ref))
            child=Path(ref)
            if not child.is_absolute(): child=path.parent/child
            child=child.resolve()
            edges.append({"from":str(path),"line":number,"include":ref,"to":str(child)})
            if not child.is_file():
                missing.append({"path":str(child),"from":str(path),"line":number,"include":ref,"chain":chain+[str(path),str(child)]})
            else: visit(child,chain+[str(path)],top=False)

    if root.is_file(): visit(root,[],top=True)
    return {"root":str(root),"files_scanned":sorted(str(x) for x in seen),"include_edges":edges,
            "missing_includes":missing,"subcircuits":subckts,"model_bins":bins,
            "model_bin_counts":{name:len(items) for name,items in bins.items()}}

def preflight(executable: str, model_lib: str) -> dict:
    exe=resolve_batch_executable(executable)
    lib=Path(model_lib).expanduser() if model_lib else None
    lib_exists=bool(lib and lib.is_file())
    lib_posix=lib.resolve().as_posix() if lib else "SKY130_MODEL_LIB_NOT_SET"
    diagnostic_dir=Path(__file__).resolve().parent/"data"/"preflight"
    diagnostic_dir.mkdir(parents=True,exist_ok=True)
    deck_path=diagnostic_dir/"model_smoke.spice"
    log_path=diagnostic_dir/"model_smoke.log"
    result={"ngspice_found":bool(exe),"ngspice_path":exe,
            "model_library_found":lib_exists,"model_library":lib_posix if lib_exists else None,
            "model_smoke_netlist":str(deck_path),"model_smoke_log":str(log_path),
            "model_include_trace":str(diagnostic_dir/"model_include_trace.json"),
            "tt_corner_load_test":"not_run","device_subckt_test":"not_run","power_sign_reference":{"status":"not_run"},"model_load_error":None,"ready":False}
    result["power_sign_reference"]=_power_sign_reference(exe,diagnostic_dir)
    # Keep this deck even when preflight is blocked, so the exact file can be inspected.
    deck=f'''* SKY130A tt model and dependency smoke test; terminals are D G S B
.option scale=1e-6
.lib "{lib_posix}" tt
VDD vdd 0 1.8
VGN gn 0 0.9
VGP gp 0 0
RDN nd 0 1Meg
RDP dp 0 1Meg
XN nd gn 0 0 sky130_fd_pr__nfet_01v8 W=0.5 L=0.15
XP dp gp vdd vdd sky130_fd_pr__pfet_01v8 W=0.5 L=0.15
.control
op
print v(nd)
print v(dp)
quit
.endc
.end
'''
    deck_path.write_text(deck,encoding="utf-8")
    if lib_exists:
        trace=trace_model_library(str(lib))
        trace_path=diagnostic_dir/"model_include_trace.json"
        trace_path.write_text(json.dumps(trace,indent=2)+"\n",encoding="utf-8")
        result["model_include_summary"]={"files_scanned":len(trace["files_scanned"]),
            "missing_includes":len(trace["missing_includes"]),"subcircuits":trace["subcircuits"],
            "model_bin_counts":trace["model_bin_counts"]}
        if trace["missing_includes"]:
            result["model_load_error"]="Missing nested model include(s); see model_include_trace.json"
        if not all(name in trace["subcircuits"] for name in ("sky130_fd_pr__nfet_01v8","sky130_fd_pr__pfet_01v8")):
            result["model_load_error"]="TT include tree lacks the expected NFET/PFET subcircuit definition(s); see model_include_trace.json"
        if not trace["model_bin_counts"]["nfet"] or not trace["model_bin_counts"]["pfet"]:
            result["model_load_error"]="TT include tree lacks the expected NMOS/PMOS .model bins; see model_include_trace.json"
    else:
        trace_path=diagnostic_dir/"model_include_trace.json"
        trace_path.write_text(json.dumps({"root":lib_posix,"status":"library file unavailable; include chain not inspected"},indent=2)+"\n",encoding="utf-8")
    if not exe:
        result["model_load_error"]="ngspice console executable was not found"
        log_path.write_text(result["model_load_error"]+"\n",encoding="utf-8")
        return result
    if not lib_exists:
        result["model_load_error"]="SKY130 model library file was not found"
        log_path.write_text(result["model_load_error"]+"\n",encoding="utf-8")
        return result
    try:
        _ensure_spiceinit(diagnostic_dir)
        proc=subprocess.run([exe,"-b",str(deck_path)],cwd=diagnostic_dir,text=True,
            encoding="utf-8",errors="replace",stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=120)
        output=proc.stdout or ""
        # Preserve the complete combined stdout/stderr, not a truncated tail.
        log_path.write_text(output,encoding="utf-8")
        bad=re.search(r"can't find model|could not open|failed to open|no such file|fatal error|unknown subckt|unknown model|error on line|^Error:",output,re.I|re.M)
        op_values=bool(re.search(r"v\(nd\)\s*=",output,re.I) and re.search(r"v\(dp\)\s*=",output,re.I))
        trace_ok=not result.get("model_include_summary",{}).get("missing_includes",-1)
        ok=proc.returncode==0 and bad is None and op_values and trace_ok and result.get("model_load_error") is None and result["power_sign_reference"].get("status")=="pass"
        result["tt_corner_load_test"]="pass" if ok else "fail"
        result["device_subckt_test"]="pass" if ok else "fail"
        if not ok:
            result["model_load_error"]=(output if output else f"ngspice exit code {proc.returncode}; no output captured")
            if result["power_sign_reference"].get("status")!="pass":
                result["model_load_error"] += "\nPower sign reference failed; see power_sign_reference.log"
        result["ready"]=ok
    except Exception as e:
        message=f"{type(e).__name__}: {e}"
        log_path.write_text(message+"\n",encoding="utf-8")
        result["tt_corner_load_test"]="fail"; result["device_subckt_test"]="fail"
        result["model_load_error"]=message
    return result

def run_netlist(netlist: str, workdir: Path, executable: str, stem: str) -> dict:
    workdir.mkdir(parents=True,exist_ok=True)
    _ensure_spiceinit(workdir)
    cir=workdir/f"{stem}.spice"; log=workdir/f"{stem}.log"; cir.write_text(netlist,encoding="utf-8")
    wave_match=re.search(r"^\s*wrdata\s+(\S+)",netlist,re.I|re.M)
    wave=(workdir/wave_match.group(1)).resolve() if wave_match else None
    if wave and wave.exists(): wave.unlink()
    exe=resolve_batch_executable(executable)
    if not exe:
        message=f"ngspice batch executable not found: {executable}"
        log.write_text(message+"\n",encoding="utf-8")
        return {"status":"failed","returncode":None,"measurements":{},"log":str(log),"error":message}
    try:
        proc=subprocess.run([exe,"-b",str(cir)],cwd=workdir,text=True,encoding="utf-8",errors="replace",
                            stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=120)
        log.write_text(proc.stdout,encoding="utf-8")
    except Exception as e:
        log.write_text(f"RUN_ERROR: {type(e).__name__}: {e}\n",encoding="utf-8")
        return {"status":"failed","returncode":None,"measurements":{},"log":str(log),"error":str(e)}
    meas={k.lower():float(v) for k,v in MEAS.findall(proc.stdout)}
    missing={"e_delivered","tpd_rise","tpd_fall","y_before","y_mid","y_after","ivdd_avg","vdd_avg"}-set(meas)
    wave_ok=bool(wave and wave.is_file() and wave.stat().st_size>0)
    has_error=re.search(r"(?:^|\n)\s*(?:Error:|Error on line|fatal error|can't find model|could not open)",proc.stdout,re.I) is not None
    status="success" if proc.returncode==0 and not missing and not has_error and wave_ok else "failed"
    return {"status":status,"returncode":proc.returncode,"measurements":meas,"missing_measurements":sorted(missing),"waveform":str(wave) if wave else None,"waveform_written":wave_ok,"log":str(log)}
