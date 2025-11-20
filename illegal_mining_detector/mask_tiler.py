import rasterio
from rasterio.windows import Window
import os
import numpy as np

# --- 1. CONFIGURATION ---
# >>> ENSURE THIS PATH POINTS EXACTLY TO YOUR ALIGNED MASK FILE <<<
# (Assuming your QGIS output was saved here)
INPUT_MASK_PATH = '/Users/admin/Desktop/illegal_mining_detector/data/raw/ghana_mining_masks.tif' 
# ------------------------------------------------------------------

# Output folder for the mask tiles (where you have 0 items currently)
OUTPUT_DIR_MASKS = 'data/tiles/masks/' 
TILE_SIZE = 256 # 256x256 pixels

# --- 2. SETUP ---
# Create output directory for masks if it doesn't exist
os.makedirs(OUTPUT_DIR_MASKS, exist_ok=True)

# --- 3. TILING FUNCTION ---
def tile_raster(input_path, output_dir, is_mask=True):
    """Tiles a large raster into 256x256 GeoTIFFs."""
    print(f"Starting tiling for: {input_path}")
    
    with rasterio.open(input_path) as src:
        num_bands = src.count
        
        # We use the dimensions of the input mask to calculate rows/cols
        rows = src.height // TILE_SIZE
        cols = src.width // TILE_SIZE
        
        tile_count = 0
        
        for i in range(rows):
            for j in range(cols):
                window = Window(j * TILE_SIZE, i * TILE_SIZE, TILE_SIZE, TILE_SIZE)
                tile_data = src.read(window=window)
                
                # Check for completely empty tiles (mostly nodata)
                if (tile_data == src.nodata).all() or (np.isnan(tile_data)).all():
                    continue

                profile = src.profile
                profile.update({
                    'height': TILE_SIZE,
                    'width': TILE_SIZE,
                    'transform': src.window_transform(window),
                    'nodata': None 
                })
                
                # --- Specific settings for masks ---
                if is_mask:
                    profile.update(dtype=rasterio.uint8)
                    tile_data = tile_data.astype(np.uint8)

                # Ensure output name matches the structure of your image tiles
                output_filename = os.path.join(output_dir, f'tile_{i:04d}_{j:04d}.tif') 
                
                with rasterio.open(output_filename, 'w', **profile) as dst:
                    # Write the single band data
                    dst.write(tile_data[0], 1) 
                
                tile_count += 1
                
        print(f"Successfully created {tile_count} tiles in {output_dir}")

# --- 4. EXECUTION ---
print("--- STARTING MASK TILING PROCESS ---")

# Tile the Label Masks (THIS IS THE ONLY PART THAT NEEDS TO RUN)
tile_raster(INPUT_MASK_PATH, OUTPUT_DIR_MASKS, is_mask=True)

print("--- MASK TILING COMPLETE! ---")