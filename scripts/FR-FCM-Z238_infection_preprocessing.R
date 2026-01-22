import flowkit as fk
import pandas as pd
import os
import re

# --- 1. Define Paths ---
base_dir = "/home/projects/dp_immunoth/data/benchmark_flow/FR-FCM-Z238"
assignment_path = os.path.join(base_dir, "FlowRepository_FR-FCM-Z238_files/Assignment.csv")
meta_path = os.path.join(base_dir, "FlowRepository_FR-FCM-Z238_files/Flowrepository_annotations_(2).csv")
fcs_dir = base_dir

# Define where you want the final CSVs to go
output_dir = os.path.join(base_dir, "FR-FCM-Z238_infection_final")

# --- 2. Load Metadata and Identify Target Files ---
print("Loading metadata...")
try:
    meta_df = pd.read_csv(meta_path, encoding='latin1')
except UnicodeDecodeError:
    meta_df = pd.read_csv(meta_path, encoding='cp1252')

target_condition = "PBMCs collected 2 Days Post onset of symptoms of chikungunya fever"
target_files = meta_df[meta_df['Sample Source Description'] == target_condition]['FCS File'].unique().tolist()

if not target_files:
    raise ValueError("No files found matching the target condition.")

print(f"Found {len(target_files)} target files to process.")

# --- 3. Load Assignment Lookup ---
assignment_lookup = pd.read_csv(assignment_path)
print(f"Lookup Table loaded: {len(assignment_lookup)} rows")

# --- 4. Define Exclusions ---
exclude_keywords = [
    'Time', 'Event_length', 'Assignment', 'Profiling', 'Cluster', 'Label',
    'DNA', 'Viability', 'Center', 'Offset', 'Width', 'Residual'
]
exclude_pattern = re.compile("|".join(exclude_keywords), re.IGNORECASE)

# --- 5. Batch Process Loop ---
for i, f_name in enumerate(target_files):
    fcs_path = os.path.join(fcs_dir, f_name)

    if not os.path.exists(fcs_path):
        print(f"Skipping missing file: {f_name}")
        continue

    print(f"\n[{i+1}/{len(target_files)}] Processing: {f_name}")

    try:
        # A. Load FCS
        sample = fk.Sample(fcs_path)
        raw_df = sample.as_dataframe(source='raw')

        # B. Flatten Column Names & Find Assignment Column
        new_cols = []
        assignment_col_name = None

        for c in raw_df.columns:
            pnn = c[0] # Technical Channel
            pns = c[1] # Marker / Label

            # Use Marker if it exists and isn't empty, otherwise Technical
            col_name = pns if (pns and pns not in ['-', '']) else pnn

            # Identify the column containing the assignment integers
            if "Assignment" in pnn or (pns and "Assignment" in pns):
                assignment_col_name = col_name

            new_cols.append(col_name)

        raw_df.columns = new_cols

        if not assignment_col_name:
            print(f"  WARNING: No 'Assignment' channel in {f_name}. Skipping.")
            continue

        # C. Merge with Lookup Table (Annotation)
        merged_df = raw_df.merge(
            assignment_lookup,
            left_on=assignment_col_name,
            right_on='Value',
            how='left'
        )

        # Rename label column
        if 'CellSubset' in merged_df.columns:
            merged_df.rename(columns={'CellSubset': 'label'}, inplace=True)
        else:
            print(f"  ERROR: Assignment CSV missing 'CellSubset' column. Skipping.")
            continue

        # D. Filter & Rename Rows
        # 1. Drop truly missing labels
        merged_df = merged_df.dropna(subset=['label'])
        merged_df = merged_df[merged_df['label'] != ""]

        # 2. RENAME "unassigned" to "Ungated"
        mask_unassigned = merged_df['label'].str.contains("unassigned", case=False)
        merged_df.loc[mask_unassigned, 'label'] = "Ungated"

        # print(f"  - Renamed {mask_unassigned.sum()} events to 'Ungated'")

        # E. Select Final Columns (Exclude Junk)
        final_cols = []
        for col in merged_df.columns:
            if col == 'label':
                final_cols.append(col)
                continue
            
            # Skip excluded columns
            if exclude_pattern.search(col):
                continue

            # Skip the merge key
            if col == 'Value':
                continue

            final_cols.append(col)

        final_df = merged_df[final_cols]

        # F. Save to CSV
        # Replace .fcs with .csv for the output filename
        output_filename = f_name.replace(".fcs", ".csv")
        output_path = os.path.join(output_dir, output_filename)

        final_df.to_csv(output_path, index=False)
        print(f"  Saved: {output_filename} ({len(final_df)} events)")

    except Exception as e:
        print(f"  ERROR processing {f_name}: {e}")

print("\n--- ALL FILES PROCESSED SUCCESSFULLY ---")
