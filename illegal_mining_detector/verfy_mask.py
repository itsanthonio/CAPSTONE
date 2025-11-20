import rasterio
import os
import numpy as np

MASK_DIR = 'data/tiles/masks/'
total_positive_pixels = 0
total_tiles_with_mining = 0
total_pixels_checked = 0

print("--- ANALYZING ALL MASK TILES ---")
for filename in os.listdir(MASK_DIR):
    if filename.endswith('.tif'):
        mask_path = os.path.join(MASK_DIR, filename)
        
        try:
            with rasterio.open(mask_path) as src:
                mask_data = src.read(1)
                
                # Count pixels with value > 0 (i.e., mining labels)
                positive_count = np.sum(mask_data > 0)
                
                if positive_count > 0:
                    total_tiles_with_mining += 1
                    
                total_positive_pixels += positive_count
                total_pixels_checked += mask_data.size
                
        except Exception as e:
            print(f"Error reading {filename}: {e}")

# --- Summary ---
print("\n--- FINAL MASK DATA SUMMARY ---")
if total_pixels_checked > 0:
    print(f"Total Tiles Analyzed: {len(os.listdir(MASK_DIR))}")
    print(f"Tiles Containing Mining Activity: {total_tiles_with_mining}")
    print(f"Total Mining Pixels (Value 1): {total_positive_pixels}")
    
    overall_percentage = (total_positive_pixels / total_pixels_checked) * 100
    print(f"Overall Mining Density: {overall_percentage:.6f}%")
else:
    print("ERROR: No valid tiles were found to analyze. Check the folder path.")