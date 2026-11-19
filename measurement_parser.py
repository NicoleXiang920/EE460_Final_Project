"""Strictly validate ngspice measurement values; missing results are never zero-filled."""
import math
def checked_metrics(result: dict, vdd: float, cycle_duration_s: float) -> dict:
    m=result.get("measurements",{})
    if result.get("status")!="success": raise ValueError(f"simulation failed ({result.get('log')})")
    required={"e_delivered","tpd_rise","tpd_fall","y_before","y_mid","y_after","ivdd_avg","vdd_avg"}
    if not required <= m.keys(): raise ValueError(f"required full-cycle measurements are missing: {sorted(required-m.keys())}")
    energy=m["e_delivered"]; current=m["ivdd_avg"]; measured_vdd=m["vdd_avg"]
    rise=m["tpd_rise"]; fall=m["tpd_fall"]; power=energy/cycle_duration_s
    if not all(math.isfinite(x) for x in (energy,current,measured_vdd,rise,fall,power)):
        raise ValueError("non-finite energy, supply, or timing measurement")
    if cycle_duration_s<=0 or energy<=0 or power>1e-2 or rise<=0 or fall<=0 or rise>=cycle_duration_s/2 or fall>=cycle_duration_s/2:
        raise ValueError(f"unreasonable full-cycle measurements: energy={energy} J, power={power} W, rise={rise} s, fall={fall} s")
    if abs(measured_vdd-vdd) > max(vdd*0.01, 1e-6):
        raise ValueError(f"measured supply voltage {measured_vdd} V differs from configured {vdd} V")
    implied_energy=-measured_vdd*current*cycle_duration_s
    if abs(energy-implied_energy)>max(abs(energy)*0.05,1e-18):
        raise ValueError(f"integrated energy/current mismatch: E={energy} J, -Vavg*Iavg*duration={implied_energy} J")
    if not all(x<vdd*.2 or x>vdd*.8 for x in (m["y_before"],m["y_mid"],m["y_after"])):
        raise ValueError("output did not reach valid logic levels before, between, and after the two transitions")
    if (m["y_before"]>vdd/2)==(m["y_mid"]>vdd/2) or (m["y_mid"]>vdd/2)==(m["y_after"]>vdd/2):
        raise ValueError("output did not cross the 50% VDD threshold in both transition directions")
    return {"energy_delivered_j":energy,"average_power_w":power,"rise_delay_s":rise,"fall_delay_s":fall,
            "propagation_delay_s":(rise+fall)/2,"average_supply_current_a":current,"average_supply_voltage_v":measured_vdd}
