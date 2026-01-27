import rasterio
from rasterio.windows import Window
import numpy as np
import os
from scipy import ndimage

# --- CONFIGURATION ---
patch_size = 256
base_path = '/Users/admin/Documents/CAPSTONE'
sar_2021_path = f"{base_path}/SAR-SENTINEL1/2021/S1_2021_Clipped_Final.tif"
sar_2024_path = f"{base_path}/SAR-SENTINEL1/2024/S1_2024_Clipped_Final.tif"
mask_2024_path = f"{base_path}/Mining_Footprint/ft2024_Aligned.tif"

output_dir = './training_data'
os.makedirs(f"{output_dir}/images", exist_ok=True)
os.makedirs(f"{output_dir}/masks", exist_ok=True)

print("--- Starting Targeted Extraction ---")

with rasterio.open(mask_2024_path) as m_src:
    mask_full = m_src.read(1)
    # Find coordinates of all mining pixels
    y_coords, x_coords = np.where(mask_full == 1)
    
    # Get unique clusters (so we don't save the same mine 1000 times)
    # We'll take every 100th pixel to spread out across the map
    targets = list(zip(x_coords, y_coords))[::100] 

with rasterio.open(sar_2021_path) as s21_src, \
     rasterio.open(sar_2024_path) as s24_src:

    count = 0
    for cx, cy in targets:
        # Calculate window centered on the mine
        window = Window(cx - patch_size//2, cy - patch_size//2, patch_size, patch_size)
        
        # Ensure window is within image bounds
        if (cx - patch_size//2 < 0 or cy - patch_size//2 < 0 or 
            cx + patch_size//2 > s21_src.width or cy + patch_size//2 > s21_src.height):
            continue

        # Read the data
        s21 = s21_src.read((1, 2), window=window)
        s24 = s24_src.read((1, 2), window=window)
        mask = mask_full[int(window.row_off):int(window.row_off+patch_size), 
                         int(window.col_off):int(window.col_off+patch_size)]

        # --- THE QGIS STRETCH ---
        # Instead of fixed numbers, we stretch based on this specific patch's reality
        def stretch(band):
            # Ignore 0s (NoData)
            valid = band[band != 0]
            if valid.size < 10: return np.zeros_like(band)
            p2, p98 = np.percentile(valid, 2), np.percentile(valid, 98)
            return np.clip((band - p2) / (p98 - p2 + 1e-6), 0, 1)

        img_stack = np.stack([stretch(s21[0]), stretch(s21[1]), 
                             stretch(s24[0]), stretch(s24[1])]).astype(np.float32)

        # Save only if the data isn't empty
        if img_stack.max() > 0:
            np.save(f"{output_dir}/images/patch_{count:05d}.npy", img_stack)
            np.save(f"{output_dir}/masks/mask_{count:05d}.npy", mask)
            count += 1

print(f"--- Processed {count} targeted mining patches. ---")