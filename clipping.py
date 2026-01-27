from osgeo import gdal
gdal.UseExceptions() 

# Define your paths
input_raster = '/Users/admin/Documents/CAPSTONE/SAR-SENTINEL1/2024/merged_sar_2024.tif'
mask_vector = '/Users/admin/Documents/CAPSTONE/Mining_Footprint/polygonized_ft2024.gpkg' 
output_raster = '/Users/admin/Documents/CAPSTONE/SAR-SENTINEL1/2024/S1_2024_Clipped_Final.tif'

# Set up the Warp options
options = gdal.WarpOptions(
    format='GTiff',
    cutlineDSName=mask_vector,      # The mask layer
    cropToCutline=True,             # Crop the extent to the mask
    dstSRS='EPSG:32630',            # Ensure output is in your UTM zone
    resampleAlg=gdal.GRIORA_NearestNeighbour, # Critical for ML: no value averaging
    multithread=True,               # Uses all CPU cores
    outputType=gdal.GDT_Float64,    # Ensures SAR decimals are kept
    creationOptions=[
        'COMPRESS=LZW',             # Lossless compression
        'TILED=YES',                # Faster for model training patches
        'PREDICTOR=3'               # Best for Float64 compression
    ]
)

print("Starting Clip...")
gdal.Warp(output_raster, input_raster, options=options)
print(f"Done! Saved to: {output_raster}")