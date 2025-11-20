import rasterio
from rasterio.windows import Window
import os
import numpy as np

# --- 1. CONFIGURATION ---
# Define input and oS1_Ratio_VV_VHutput paths
INPUT_IMAGE_PATH = '/Users/admin/Desktop/illegal_mining_detector/S1_Ratio_VV_VH.tif' # Your GEE output
INPUT_MASK_PATH = '/Users/admin/Desktop/illegal_mining_detector/data/raw/aligned.tif' # Your aligned mask
OUTPUT_DIR_IMAGES = 'data/tiles/images/'
OUTPUT_DIR_MASKS = 'data/tiles/masks/'
TILE_SIZE = 256 # 256x256 pixels

# --- 2. SETUP ---
# Create output directories if they don't exist
os.makedirs(OUTPUT_DIR_IMAGES, exist_ok=True)
os.makedirs(OUTPUT_DIR_MASKS, exist_ok=True)

# --- 3. TILING FUNCTION ---
def tile_raster(input_path, output_dir, is_mask=False):
    """Tiles a large raster into 256x256 GeoTIFFs."""
    print(f"Starting tiling for: {input_path}")
    
    with rasterio.open(input_path) as src:
        # Determine the number of bands (2 for image, 1 for mask)
        num_bands = src.count
        
        # Calculate how many tiles fit
        rows = src.height // TILE_SIZE
        cols = src.width // TILE_SIZE
        
        tile_count = 0
        
        for i in range(rows):
            for j in range(cols):
                # Define the pixel window for the tile
                window = Window(j * TILE_SIZE, i * TILE_SIZE, TILE_SIZE, TILE_SIZE)
                
                # Read the data for the current window
                tile_data = src.read(window=window)
                
                # Skip tiles that are mostly nodata (optional but helpful for empty edge areas)
                if (tile_data == src.nodata).all():
                    continue

                # --- Write the new tile ---
                
                # Update the profile for the new tile
                profile = src.profile
                profile.update({
                    'height': TILE_SIZE,
                    'width': TILE_SIZE,
                    'transform': src.window_transform(window),
                    'nodata': None # Remove nodata value for clean ML input
                })
                
                # For masks, ensure the output data type is Byte (Int8) for clean binary labels
                if is_mask:
                    profile.update(dtype=rasterio.uint8)
                    tile_data = tile_data.astype(np.uint8)

                # Define the output filename
                output_filename = os.path.join(output_dir, f'tile_{i:04d}_{j:04d}.tif')
                
                with rasterio.open(output_filename, 'w', **profile) as dst:
                    # Write the data. If 2 bands, write both. If 1 band, write one.
                    if num_bands == 1:
                        dst.write(tile_data[0], 1)
                    else:
                        dst.write(tile_data)
                
                tile_count += 1
                
        print(f"Successfully created {tile_count} tiles in {output_dir}")

# --- 4. EXECUTION ---
print("--- STARTING TILING PROCESS ---")

# Tile the Image Features
tile_raster(INPUT_IMAGE_PATH, OUTPUT_DIR_IMAGES, is_mask=False)

# Tile the Label Masks
# Note: This relies on the masks and images having the same dimensions (which they do after your alignment!)
tile_raster(INPUT_MASK_PATH, OUTPUT_DIR_MASKS, is_mask=True)

print("--- TILING COMPLETE! Data is ready for ML training. ---")