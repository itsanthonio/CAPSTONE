import rasterio
import numpy as np

# Path to one of the tiles (adjust the name if necessary)
# Check if your output tiles are named 'tile_0001_0001.tif' or similar
TILE_PATH = 'data/tiles/images/tile_0013_0086.tif' 

try:
    with rasterio.open(TILE_PATH) as src:
        # 1. Print File Structure
        print("--- FILE STRUCTURE CHECK ---")
        print(f"Bands in file: {src.count}")
        print(f"Width/Height: {src.width} x {src.height}")
        print(f"Data Type: {src.dtypes}")

        # 2. Read the VV Ratio band (Band 1)
        vv_ratio_data = src.read(1) 
        
        # 3. Print Statistical Proof that the data exists
        print("\n--- VV RATIO DATA CHECK (Band 1) ---")
        print(f"Minimum Value: {np.nanmin(vv_ratio_data):.4f}")
        print(f"Maximum Value: {np.nanmax(vv_ratio_data):.4f}")
        print(f"Mean Value: {np.nanmean(vv_ratio_data):.4f}")
        
except rasterio.RasterioIOError:
    print(f"ERROR: Could not open the file at {TILE_PATH}. The file might be corrupted.")
except FileNotFoundError:
    print(f"ERROR: File not found at {TILE_PATH}. Check your file path and name.")