"""Static CMOS standard-gate netlist and topology generation for SKY130A."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from itertools import product
from pathlib import Path

@dataclass
class Mos:
    name: str
    kind: str
    drain: str
    gate: str
    source: str
    bulk: str
    width_um: float
    length_um: float

GATES = ("NAND2", "NAND3", "NOR2", "NOR3", "XOR2", "AOI21", "AOI22")

def _mos(name, kind, d, g, s, b, w, l):
    return Mos(name, kind, d, g, s, b, w, l)

def build_gate(gate: str, widths: dict[str, float], length_um: float = .15) -> list[Mos]:
    """Return an explicit CMOS transistor list. Internal NAND nodes implement XOR2."""
    g = gate.upper(); t=[]
    def add(kind, d, gate, s, b, key):
        # The dataset stores widths by emitted device name (M1, M2, ...).
        # Keep the topology-specific key as a fallback for older hand-built callers.
        name=f"M{len(t)+1}"
        t.append(_mos(name, kind, d, gate, s, b, widths.get(name, widths.get(key, .5)), length_um))
    if g.startswith("NAND"):
        n=int(g[-1]);
        for i in range(n): add("n", "Y" if i==0 else f"nx{i}", f"A{i+1}", "VSS" if i==n-1 else f"nx{i+1}", "VSS", f"n{i+1}")
        for i in range(n): add("p", "Y", f"A{i+1}", "VDD", "VDD", f"p{i+1}")
    elif g.startswith("NOR"):
        n=int(g[-1]);
        for i in range(n): add("n", "Y", f"A{i+1}", "VSS", "VSS", f"n{i+1}")
        for i in range(n): add("p", "Y" if i==0 else f"px{i}", f"A{i+1}", "VDD" if i==n-1 else f"px{i+1}", "VDD", f"p{i+1}")
    elif g=="XOR2":
        # Four 2-input NAND gates form a static-CMOS XOR; each NAND is expanded to 4 MOSFETs.
        nets=[("A1","A2","n1"),("A1","n1","n2"),("A2","n1","n3"),("n2","n3","Y")]
        for idx,(a,b,y) in enumerate(nets):
            add("n",f"{y}_n",a,"VSS","VSS",f"n{idx+1}a"); add("n",y,b,f"{y}_n","VSS",f"n{idx+1}b")
            add("p",y,a,"VDD","VDD",f"p{idx+1}a"); add("p",y,b,"VDD","VDD",f"p{idx+1}b")
    elif g=="AOI21":
        # Y = !((A1&A2) | A3). PDN: series pair || single. PUN is its dual.
        add("n","nx","A1","VSS","VSS","n1"); add("n","Y","A2","nx","VSS","n2"); add("n","Y","A3","VSS","VSS","n3")
        add("p","px","A1","VDD","VDD","p1"); add("p","px","A2","VDD","VDD","p2")
        add("p","Y","A3","px","VDD","p3")
    elif g=="AOI22":
        # Y = !((A1&A2) | (A3&A4)); PDN is two series branches in parallel.
        add("n","nx1","A1","VSS","VSS","n1"); add("n","Y","A2","nx1","VSS","n2")
        add("n","nx2","A3","VSS","VSS","n3"); add("n","Y","A4","nx2","VSS","n4")
        # Dual PUN: (A1 || A2) series (A3 || A4).
        for pair,(a,b) in enumerate((("A1","A2"),("A3","A4")),1):
            for k,x in enumerate((a,b),1): add("p", "Y" if pair==2 else "px", x, "VDD" if pair==1 else "px", "VDD", f"p{pair}{k}")
    else: raise ValueError(f"Unsupported gate: {gate}")
    return t

def input_names(gate: str) -> list[str]:
    return [f"A{i}" for i in range(1, (3 if gate in ("NAND3","NOR3","AOI21") else 4 if gate=="AOI22" else 2)+1)]

def truth(gate: str, vals: dict[str,int]) -> int:
    a=[vals[n] for n in input_names(gate)]
    return {"NAND2":lambda:1-(a[0]&a[1]),"NAND3":lambda:1-(a[0]&a[1]&a[2]),
            "NOR2":lambda:1-(a[0]|a[1]),"NOR3":lambda:1-(a[0]|a[1]|a[2]),
            "XOR2":lambda:a[0]^a[1],"AOI21":lambda:1-((a[0]&a[1])|a[2]),
            "AOI22":lambda:1-((a[0]&a[1])|(a[2]&a[3]))}[gate]()

def validate_boolean(gate: str, mos: list[Mos]) -> bool:
    """Ideal switch-level check of the generated MOS network for every input vector."""
    names=input_names(gate); internal=sorted({m.gate for m in mos}-set(names))
    for code in range(1<<len(names)):
        primary={n:(code>>i)&1 for i,n in enumerate(names)}; expected=truth(gate,primary); valid=False
        # Resolve driven internal NAND outputs by trying their Boolean states. Floating
        # diffusion nodes are allowed; every internal control net and Y must resolve.
        for bits in product((0,1), repeat=len(internal)):
            vals={**primary,**dict(zip(internal,bits))}; adjacency={}
            for m in mos:
                on=(vals.get(m.gate,0)==1) if m.kind=="n" else (vals.get(m.gate,0)==0)
                if on:
                    adjacency.setdefault(m.drain,set()).add(m.source); adjacency.setdefault(m.source,set()).add(m.drain)
            def level(node):
                todo=[node]; seen=set(todo); rails=set()
                while todo:
                    x=todo.pop()
                    if x in ("VDD","VSS"): rails.add(x)
                    for y in adjacency.get(x,()):
                        if y not in seen: seen.add(y); todo.append(y)
                return 1 if rails=={"VDD"} else 0 if rails=={"VSS"} else None
            resolved={node:level(node) for node in ["Y",*internal]}
            if all(value is not None for value in resolved.values()) and resolved["Y"]==expected and all(resolved[n]==vals[n] for n in internal):
                valid=True; break
        if not valid:return False
    return True

def graph_record(gate: str, mos: list[Mos]) -> dict:
    # Ordered terminals remain explicit for role-aware graph construction.
    return {"gate_type":gate,"nodes":sorted({n for m in mos for n in (m.drain,m.gate,m.source,m.bulk)}|set(input_names(gate))),
            "transistors":[asdict(m) for m in mos],"terminal_roles":["drain","gate","source","bulk"]}

def render_netlist(gate: str, mos: list[Mos], lib: str, corner: str, vdd: float,
                   load: float, slew: float, step: float, stop: float,
                   scenario: dict[str,int], target_input: str, initial_y: int,
                   waveform_file: str = "waveform.dat", measure_start: float = 0.5e-9,
                   measure_end: float = 1.5e-9) -> str:
    if corner.lower() != "tt": raise ValueError("SKY130 dataset netlists require the tt corner")
    if not lib: raise ValueError("An ngspice-compatible SKY130 model library path is required")
    lib=Path(lib).expanduser().resolve().as_posix()
    names=input_names(gate); edge=slew
    period=measure_end-measure_start
    if period <= 4*edge: raise ValueError("measurement interval must contain a full cycle with edge settling time")
    first_edge=measure_start+period/4
    second_edge=measure_start+3*period/4
    if stop < measure_end: raise ValueError("transient stop time must include the full measurement cycle")
    if scenario[target_input] != 0:
        raise ValueError("cycle stimulus requires target input to start low and return low")
    y0=truth(gate,scenario); y1=1-y0
    if initial_y != y0: raise ValueError("initial_y does not match the sensitized input vector")
    sources=[]
    for name in names:
        before=scenario[name]
        if name==target_input:
            sources.append(f"V{name} {name} 0 PWL(0 0 {first_edge:.9g} 0 {first_edge+edge:.9g} {vdd:.9g} {second_edge:.9g} {vdd:.9g} {second_edge+edge:.9g} 0 {stop:.9g} 0)")
        else:
            sources.append(f"V{name} {name} 0 {before*vdd:.9g}")
    lines=[f"* {gate}; SKY130A; static CMOS",".option scale=1e-6",f'.lib "{lib}" {corner}',
           f"VDD VDD 0 {vdd}","VSS VSS 0 0",*sources]
    for m in mos:
        model="sky130_fd_pr__nfet_01v8" if m.kind=="n" else "sky130_fd_pr__pfet_01v8"
        # SKY130's ngspice device cells are .subckt definitions, not .model cards.
        # X instances pass terminals in the declared order: D, G, S, B.
        # SKY130 uses a 1e-6 scale; dimensions are numeric microns, without `u` suffixes.
        lines.append(f"X{m.name} {m.drain} {m.gate} {m.source} {m.bulk} {model} W={m.width_um} L={m.length_um}")
    lines += [f"CLOAD Y 0 {load:.9g}",".temp 25",".control","set filetype=ascii",f"tran {step:.9g} {stop:.9g}",
              # ngspice defines I(VDD) positive into the voltage source's + terminal.
              # With VDD VDD 0 ..., a delivering supply therefore has I(VDD) < 0.
              # Keep the raw current measurement so this convention is auditable.
              f"meas tran ivdd_avg AVG i(VDD) from={measure_start:.9g} to={measure_end:.9g}",
              f"meas tran vdd_avg AVG v(VDD) from={measure_start:.9g} to={measure_end:.9g}",
              "let psupply = -v(VDD)*i(VDD)",
              f"meas tran e_delivered INTEG psupply from={measure_start:.9g} to={measure_end:.9g}"]
    rising_input_edge="RISE" if y0==0 else "FALL"
    falling_input_edge="FALL" if y0==0 else "RISE"
    lines += [f"meas tran tpd_rise TRIG v({target_input}) VAL={vdd/2:.9g} {rising_input_edge}=1 TARG v(Y) VAL={vdd/2:.9g} RISE=1",
              f"meas tran tpd_fall TRIG v({target_input}) VAL={vdd/2:.9g} {falling_input_edge}=1 TARG v(Y) VAL={vdd/2:.9g} FALL=1",
              f"meas tran y_before FIND v(Y) AT={measure_start+period/8:.9g}",
              f"meas tran y_mid FIND v(Y) AT={measure_start+5*period/8:.9g}",
              f"meas tran y_after FIND v(Y) AT={measure_end-period/8:.9g}",
              "set wr_singlescale",f"wrdata {waveform_file} "+" ".join([f"v({n})" for n in input_names(gate)]+["v(Y)","i(VDD)","psupply"]),"quit",".endc",".end"]
    return "\n".join(lines)+"\n"
