import flowkit as fk
import pandas as pd
import numpy as np
import glob
import os

# --- 1. Define Paths ---
# Adjust these paths to your specific environment
base_dir = "/home/projects/dp_immunoth/data/benchmark_flow/FR-FCM-Z2KP"
results_healthy_dir = os.path.join(base_dir, "FR-FCM-Z2KP_healthy_final")
results_virus_dir = os.path.join(base_dir, "FR-FCM-Z2KP_virus_final")
wsp_path = os.path.join(base_dir, "attachments/01-May-2020_Human_COVID_analysis_template.wsp")
fcs_dir = os.path.join(base_dir, "fcs_files")

# Create directories if they don't exist
os.makedirs(results_healthy_dir, exist_ok=True)
os.makedirs(results_virus_dir, exist_ok=True)

# --- 2. Setup Workspace and Strategy ---
print("Initializing Workspace and Gating Strategy...")
ws = fk.Workspace(wsp_path, fcs_samples=fcs_dir)
sample_ids = ws.get_sample_ids()
# We assume the first strategy in the WSP is the template for all
g_strat = ws.get_gating_strategy(sample_ids[0])

# --- 3. Define Helper Function ---
def find_deepest(row, levels):
    """Finds the gate with the highest hierarchy level that is True."""
    true_gates = row.index[row].tolist()
    if not true_gates:
        return "ungated"
    return max(true_gates, key=lambda g: levels.get(g, 0))

# --- 4. Define the Batch Processing Function ---
def process_batch(file_list, save_dir, group_label):
    print(f"\n--- Processing {group_label} Samples ({len(file_list)} files) ---")

    # Structural parameters to always keep
    structural_params = ['FSC-A', 'FSC-H', 'FSC-W', 'SSC-A', 'SSC-H', 'SSC-W']

    for i, fcs_path in enumerate(file_list):
        try:
            # Create a shorter, cleaner name for the file
            raw_name = os.path.basename(fcs_path)
            short_name = raw_name.replace("export_COVID19 samples ", "").replace(".fcs", "")
            print(f"[{i+1}/{len(file_list)}] Gating: {short_name}...")

            # A. Load Sample
            sample = fk.Sample(fcs_path)

            # B. Apply Gating Strategy
            gs_results = g_strat.gate_sample(sample)

            # C. Systematic Column Selection (The ML-Ready Logic)
            raw_df = sample.as_dataframe(source='raw')

            cols_to_keep = []
            new_column_names = []

            for col_tuple in raw_df.columns:
                pnn = col_tuple[0] # Technical Name
                pns = col_tuple[1] # Marker Name

                keep = False
                final_name = pnn

                # RULE 1: Keep Biological Markers (Must have a name, not '-' or '')
                if pns and pns not in ["-", ""]:
                    keep = True
                    final_name = pns

                # RULE 2: Keep Structural Parameters
                elif pnn in structural_params:
                    keep = True
                    final_name = pnn

                # RULE 3: Explicitly DROP 'Time' (Noise for ML) and 'livedead' (Target leakage/QC)
                if 'Time' in pnn or 'livedead' in str(pns).lower():
                    keep = False

                if keep:
                    cols_to_keep.append(col_tuple)
                    new_column_names.append(final_name)

            # Apply Filter & Rename
            final_df = raw_df[cols_to_keep].copy()
            final_df.columns = new_column_names

            # D. Generate Labels (Target Variable)
            gate_names = gs_results.report['gate_name'].unique()
            gate_levels = gs_results.report.set_index('gate_name')['level'].to_dict()

            gate_masks = []
            for g_name in gate_names:
                gate_masks.append(gs_results.get_gate_membership(g_name))

            gate_df = pd.DataFrame(np.array(gate_masks).T, columns=gate_names)

            # Assign Label
            final_df['label'] = gate_df.apply(lambda r: find_deepest(r, gate_levels), axis=1)

            # E. Clean Rows
            # Remove rows with empty labels
            final_df = final_df[final_df['label'] != ""]

            # F. Save to CSV
            output_file = os.path.join(save_dir, f"{short_name}_annotated.csv")
            final_df.to_csv(output_file, index=False)
            # print(f"Saved: {os.path.basename(output_file)} ({len(final_df)} events)")

        except Exception as e:
            print(f"ERROR processing {raw_name}: {e}")

# --- 5. Categorize Samples & Run ---
all_samples = glob.glob(os.path.join(fcs_dir, "*.fcs"))

# Filter Healthy vs Virus based on filename
healthy_files = [p for p in all_samples if "_HC_" in os.path.basename(p)]
virus_files = [p for p in all_samples if "_HC_" not in os.path.basename(p)]

if not all_samples:
    print("No FCS files found! Check your paths.")
else:
    # Run Healthy
    process_batch(healthy_files, results_healthy_dir, "HEALTHY")

    # Run Virus
    process_batch(virus_files, results_virus_dir, "VIRUS")

    print("\nAll samples processed successfully.")
