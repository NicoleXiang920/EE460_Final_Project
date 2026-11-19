# SKY130 CMOS gate SPICE dataset generator

This project generates transistor-level static CMOS netlists, runs ngspice, and writes measured labels plus role-aware MOS graphs. It covers NAND2/3, NOR2/3, XOR2, AOI21, and AOI22. It does not train an ML model or optimize device sizes. SKY130 FETs are instantiated as `X` subcircuits using the PDK terminal order `D G S B`.

## Technology and simulator setup

The model source is the open SKY130 PDK. The raw `skywater-pdk-libs-sky130_fd_pr` model export is not directly ngspice-ready; use the ngspice-compatible model files installed by [open_pdks](https://github.com/RTimothyEdwards/open_pdks) (commonly `$PDK_ROOT/sky130A/libs.tech/ngspice/sky130.lib.spice`). SKY130 documents its SPICE model support [here](https://skywater-pdk.readthedocs.io/en/main/sim.html). The pipeline uses the `tt` corner and model names `sky130_fd_pr__nfet_01v8` and `sky130_fd_pr__pfet_01v8`; preflight loads that corner and instantiates both device subcircuits to check that nested model includes resolve. SKY130 uses a `1e-6` scale, so transistor W/L are emitted as bare micron values (for example `W=0.5 L=0.15`), not with an additional `u` suffix. No compact-model parameters are defined or changed.

On Windows, batch runs select `ngspice_con.exe` (including when `config.yaml` names `ngspice` or `ngspice.exe`). Preflight writes its exact deck and complete simulator output to `data/preflight/model_smoke.spice` and `data/preflight/model_smoke.log` for debugging.

On Debian/Ubuntu, install ngspice with `sudo apt-get update && sudo apt-get install ngspice git make` (or use the package for your OS). Install the SKY130A ngspice model set using the open_pdks setup instructions, or use an existing installation. Point the pipeline at the installed library:

```bash
export SKY130_MODEL_LIB="$PDK_ROOT/sky130A/libs.tech/ngspice/sky130.lib.spice"
python3 -m pip install -r requirements.txt
```

`SKY130_MODEL_LIB` can be replaced with `--model-lib /absolute/path/to/sky130.lib.spice`. The preflight rejects missing simulator/model files before any measurement is created. The model library and corner are external inputs; no model parameters are copied into this repository.

## Commands

```bash
# First run one NAND2 transient and verify power/delay, waveform, and model loading.
python3 smoke_test.py

# Generate 20 samples, approximately balanced over seven topologies.
# Validate one full-cycle sample for each of the seven gate topologies first.
python3 dataset_generator.py pilot --samples 7

# After reviewing all seven topology results, run the 20-sample pilot.
python3 dataset_generator.py pilot

# Or choose topology-specific pilot counts (these replace the corresponding defaults).
python3 dataset_generator.py pilot --count NAND2=4 --count XOR2=4

# Validate saved CSV structure and summarize statuses.
python3 validate_dataset.py data/pilot/dataset.csv
```

The full command is intentionally gated until the pilot has been reviewed:

```bash
python3 dataset_generator.py full --confirm-full
# Or specify the total or per-gate quantities:
python3 dataset_generator.py full --samples 1400 --confirm-full
python3 dataset_generator.py full --count NAND2=200 --count NAND3=200 --count NOR2=200 --count NOR3=200 --count XOR2=200 --count AOI21=200 --count AOI22=200 --confirm-full
```

Do not run `full` until the pilot CSV, validation report, waveforms, and ngspice logs have been inspected. Each sample runs one complete two-edge input cycle. With the default settings, the target input rises near 1.0 ns and falls near 2.0 ns; the measurement interval is 0.5 to 2.5 ns, so its endpoints have the same settled input state. Power is computed from net energy: integrate `-V(VDD)*I(VDD)` over that interval, then divide by its duration. This does not take absolute values or flip signs. The same transient measures output rise and fall delays separately from the corresponding input/output 50% VDD crossings. The other inputs are held at values that sensitize the selected gate input, and measured output levels before, between, and after the edges are checked against the truth table. Missing, failed, non-switching, or Boolean-inconsistent results are marked failed and never replaced by zero. Missing, failed, non-switching, or Boolean-inconsistent results are marked failed and are never replaced by zero.

## Sizing and reproducibility

Python's `random.Random` is seeded from `config.yaml` (`4602026`). Each MOSFET width is sampled independently on a 0.05 um grid from 0.15 to 2.00 um, with channel length fixed at 0.15 um. Sampled `M1`, `M2`, ... widths are applied directly to matching devices, and duplicate full sizing configurations for a topology are rejected within a run. Each row records exact netlist paths, SHA-256 hashes, raw per-run measurements, logs, and waveforms to make sample association auditable. These are the initial SKY130 device geometry bounds used by this project; adjust only after confirming the chosen model's allowed geometry and the course's sizing constraints. `gate_generator.py` creates the explicit MOS connectivity. An exhaustive ideal-switch truth-table check is performed on every generated topology before running SPICE. The switch check validates logical topology; accepted numerical labels still come only from ngspice.

## Outputs and schema

Outputs are stored under `data/pilot/` or `data/full/`:

- `dataset.csv`: one row per transistor sizing. `energy_delivered_j` is integrated over one full input cycle; `average_power_w` is that energy divided by the cycle duration. `rise_delay_s` and `fall_delay_s` are measured independently within the same cycle. Units appear in column names where applicable; width/length are um, supply V, load F, energy J, power W, delay s.
- `graphs/<sample_id>.json`: MOS transistor nodes plus explicit drain, gate, source, and bulk roles, transistor type and dimensions.
- `spice/<sample>_<rise|fall>.spice`: exact netlists used for simulation.
- `spice/<sample>_<rise|fall>.log`: ngspice stdout/stderr.
- `spice/<sample>_<rise|fall>_wave.dat`: input/output waveform data for manual inspection.
- `validation_report.json`: preflight state and sample success/failure report.

CSV fields include sample/topology IDs, gate type, transistor count, per-device widths and types, connectivity, supply/load, input transition conditions, average/rise/fall/mean delay, simulation status, artifact paths, and the power interval. The connectivity and netlist artifacts retain enough information to reconstruct each circuit. `graph_converter.py` stores terminals as labeled roles for later PyTorch Geometric conversion.

## Pilot status in this workspace

The current workspace did not have `ngspice` or SKY130A model files installed at inspection. The code therefore cannot produce measured pilot results here. Run the seven-sample topology check first, inspect every report/log/waveform, then run the 20-sample pilot only after all seven pass. No simulation labels should be inferred from the generated decks or ideal Boolean checks.
