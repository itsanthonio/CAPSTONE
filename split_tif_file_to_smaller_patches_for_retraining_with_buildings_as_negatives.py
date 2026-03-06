# ===================================================================
# SPLIT BUILDING NEGATIVE TIF FILES TO PATCHES
# Processes all 20 downloaded TIF files
# Empty masks (all zeros) — no mining in buildings
# ===================================================================

import rasterio
import numpy as np
from pathlib import Path
from tqdm import tqdm
import matplotlib.pyplot as plt
import json
import random


def sample_patches_from_tif(tif_path, images_dir, masks_dir,
                             start_index=0,
                             target_patches=25,
                             patch_size=256,
                             max_nodata_pct=0.05):
    print(f"\nProcessing: {Path(tif_path).name}")

    with rasterio.open(tif_path) as src:
        print(f"  Image size: {src.width} x {src.height}")
        print(f"  Bands: {src.count}")

        height       = src.height
        width        = src.width
        num_bands    = src.count
        nodata_value = src.nodata

        valid_y_max = height - patch_size
        valid_x_max = width  - patch_size

        if valid_y_max <= 0 or valid_x_max <= 0:
            print("  Image too small, skipping")
            return []

        saved_patches = []
        attempts      = 0
        max_attempts  = target_patches * 30
        rejected      = 0

        random.seed(42)

        with tqdm(total=target_patches, desc=f"  Patches") as pbar:

            while len(saved_patches) < target_patches and attempts < max_attempts:
                attempts += 1

                y = random.randint(0, valid_y_max)
                x = random.randint(0, valid_x_max)

                window = rasterio.windows.Window(x, y, patch_size, patch_size)

                try:
                    patch_data = src.read(window=window)             # (bands, H, W)
                    patch_data = np.transpose(patch_data, (1, 2, 0)) # (H, W, bands)

                    if patch_data.shape != (patch_size, patch_size, num_bands):
                        continue

                    # Build nodata mask BEFORE nan_to_num
                    if nodata_value is not None:
                        nodata_mask = (patch_data == nodata_value).any(axis=2)
                        nodata_mask |= (np.abs(patch_data) > 1e10).any(axis=2)
                        nodata_mask |= np.isnan(patch_data).any(axis=2)
                    else:
                        nodata_mask = (np.abs(patch_data) > 1e10).any(axis=2)
                        nodata_mask |= np.isnan(patch_data).any(axis=2)

                    # Skip if too much NoData
                    nodata_pct = nodata_mask.sum() / (patch_size ** 2)
                    if nodata_pct > max_nodata_pct:
                        rejected += 1
                        continue

                    # NOW replace nodata with 0
                    patch_data = np.nan_to_num(patch_data, nan=0.0, posinf=0.0, neginf=0.0)

                    # Replace NoData pixels with 0
                    img_patch = patch_data.copy()
                    for b in range(img_patch.shape[2]):
                        img_patch[:, :, b][nodata_mask] = 0

                    # Per-patch percentile normalization (matches training exactly)
                    img_norm = np.zeros_like(img_patch, dtype=np.float32)

                    for b in range(img_patch.shape[2]):
                        band_data    = img_patch[:, :, b]
                        valid_pixels = band_data[~nodata_mask]

                        if len(valid_pixels) > 10:
                            p2, p98 = np.percentile(valid_pixels, [2, 98])
                            if p98 > p2:
                                normalized = (band_data - p2) / (p98 - p2)
                                img_norm[:, :, b] = np.clip(normalized, 0, 1)
                            else:
                                img_norm[:, :, b] = 0.5
                        else:
                            img_norm[:, :, b] = 0.5

                    # Quality checks
                    if (np.isnan(img_norm).any() or
                        np.isinf(img_norm).any() or
                        (img_norm < 0).any() or
                        (img_norm > 1).any() or
                        (img_norm == 0).all() or
                        np.allclose(img_norm, img_norm.flat[0])):
                        rejected += 1
                        continue

                    # Save with global patch index so nothing overwrites
                    patch_id = start_index + len(saved_patches)
                    filename = f"building_negative_patch_{patch_id:04d}.npy"

                    np.save(images_dir / filename, img_norm.astype(np.float32))
                    np.save(masks_dir  / filename, np.zeros((patch_size, patch_size), dtype=np.uint8))

                    saved_patches.append(filename)
                    pbar.update(1)

                except Exception:
                    rejected += 1
                    continue

        print(f"  Saved: {len(saved_patches)} | Rejected: {rejected}")
        return saved_patches


def visualize_sample(images_dir, masks_dir, filename):
    patch = np.load(images_dir / filename)
    mask  = np.load(masks_dir  / filename)

    fig, axes = plt.subplots(1, 4, figsize=(16, 4))
    fig.suptitle(filename, fontsize=10)

    rgb = patch[:, :, [2, 1, 0]]
    axes[0].imshow(np.clip(rgb, 0, 1))
    axes[0].set_title('RGB')
    axes[0].axis('off')

    axes[1].imshow(patch[:, :, 3], cmap='gray')
    axes[1].set_title('NIR')
    axes[1].axis('off')

    axes[2].imshow(patch[:, :, 8], cmap='RdYlBu_r')
    axes[2].set_title('BSI')
    axes[2].axis('off')

    axes[3].imshow(mask, cmap='gray', vmin=0, vmax=1)
    axes[3].set_title(f'Mask (sum={mask.sum()})')
    axes[3].axis('off')

    plt.tight_layout()
    save_path = images_dir.parent / f"{Path(filename).stem}_preview.png"
    plt.savefig(save_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  Preview saved: {save_path.name}")


# ===================================================================
# MAIN
# ===================================================================

if __name__ == "__main__":

    download_dir = Path(r'C:\Users\mcnob\Documents\Ashesi A\Cappy Cap\Mining_Negatives_Training_Buildings_HLS')
    output_dir   = Path(r'C:\Users\mcnob\Documents\Ashesi A\Cappy Cap\building_negative_patches')

    PATCHES_PER_FILE = 25

    images_dir = output_dir / 'images'
    masks_dir  = output_dir / 'masks'
    images_dir.mkdir(parents=True, exist_ok=True)
    masks_dir.mkdir(parents=True, exist_ok=True)

    tif_files = sorted(download_dir.glob('*.tif'))

    if not tif_files:
        print("No TIF files found!")
        print(f"Looking in: {download_dir}")
        exit()

    print("BUILDING NEGATIVE PATCH EXTRACTION")
    print(f"Found {len(tif_files)} TIF file(s)")
    print(f"Patches per file: {PATCHES_PER_FILE}")
    print(f"Target total: {len(tif_files) * PATCHES_PER_FILE}")

    all_patches  = []
    patch_offset = 0

    for tif_file in tif_files:
        patches = sample_patches_from_tif(
            tif_path       = tif_file,
            images_dir     = images_dir,
            masks_dir      = masks_dir,
            start_index    = patch_offset,
            target_patches = PATCHES_PER_FILE,
            patch_size     = 256
        )
        all_patches.extend(patches)
        patch_offset += len(patches)

    print("\nEXTRACTION SUMMARY")
    print(f"TIF files processed : {len(tif_files)}")
    print(f"Total patches saved : {len(all_patches)}")

    if not all_patches:
        print("No patches extracted. Check your TIF files.")
        exit()

    sample = np.load(images_dir / all_patches[0])
    mask   = np.load(masks_dir  / all_patches[0])

    print(f"\nSample verification:")
    print(f"  Image shape : {sample.shape}")
    print(f"  Image range : [{sample.min():.4f}, {sample.max():.4f}]")
    print(f"  Mask shape  : {mask.shape}")
    print(f"  Mask sum    : {mask.sum()}  <- should be 0")

    for idx in [0, len(all_patches) // 2, -1]:
        visualize_sample(images_dir, masks_dir, all_patches[idx])

    metadata = {
        'source_files'    : [f.name for f in tif_files],
        'total_patches'   : len(all_patches),
        'patches_per_file': PATCHES_PER_FILE,
        'patch_size'      : 256,
        'bands'           : int(sample.shape[2]),
        'label'           : 'negative (buildings)',
        'mask_values'     : 'all zeros — no mining',
        'normalization'   : 'per-patch percentile (2nd-98th)',
        'building_types'  : [
            'Industrial / large rooftops',
            'Dense residential settlements',
            'Institutional / campuses',
            'Rural / isolated compounds'
        ],
        'output': {
            'images': str(images_dir),
            'masks' : str(masks_dir)
        }
    }

    with open(output_dir / 'building_negative_metadata.json', 'w') as f:
        json.dump(metadata, f, indent=2)

    print(f"\nDone!")
    print(f"   Images : {images_dir}")
    print(f"   Masks  : {masks_dir}")
    print(f"\nNext: merge with existing negatives and retrain.")