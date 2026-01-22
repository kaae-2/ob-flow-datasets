library(data.table)

# 1. Define paths
input_dir <- "/home/projects/dp_immunoth/data/benchmark_flow/FR-FCM-Z2KP/Healthy/"
output_dir <- "/home/projects/dp_immunoth/data/benchmark_flow/FR-FCM-Z2KP/FR-FCM-Z2KP_healthy_final/"

# Create output directory if it doesn't exist
if (!dir.exists(output_dir)) dir.create(output_dir, recursive = TRUE)

# 2. List all CSV files in the folder
files <- list.files(input_dir, pattern = "\\.csv$", full.names = FALSE)

# 3. Loop through each file
for (f in files) {
  # --- Load and Process ---
  # Construct full path for reading
  dt <- fread(paste0(input_dir, f))
  
  # Rename label column
  if ("final_annotation" %in% names(dt)) {
    setnames(dt, "final_annotation", "label")
  }
  
  # Remove rows with no label
  dt <- dt[label != ""]

  
  # --- Rename File ---
  new_name <- gsub(" ", "_", f)
  new_name <- paste0("FR-FCM-Z2KP_Healthy_", new_name)
  
  # --- Save ---
  fwrite(dt, paste0(output_dir, new_name))
  message(paste("Processed:", f, "->", new_name))
}