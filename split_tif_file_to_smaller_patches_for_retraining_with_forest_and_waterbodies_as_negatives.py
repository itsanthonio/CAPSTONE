# ===================================================================
# SPLIT NEGATIVE TIF FILES TO PATCHES
# Matches original training data extraction exactly
# ===================================================================

import rasterio
import numpy as np
from pathlib import Path
from tqdm import tqdm
import matplotlib.pyplot as plt
import json
import random


def sample_patches_from_tif(tif_path, output_dir,
                            target_patches=500,
                            patch_size=256,
                            max_nodata_pct=0.3):
    """
    Sample patches from TIF - EXACT MATCH to training extraction
    """

    output_dir = Path(output_dir)
    images_dir = output_dir / 'images'
    masks_dir = output_dir / 'masks'

    images_dir.mkdir(exist_ok=True, parents=True)
    masks_dir.mkdir(exist_ok=True, parents=True)

    print(f"\nProcessing: {Path(tif_path).name}")
    print("=" * 60)

    with rasterio.open(tif_path) as src:
        print(f"Image size: {src.width} x {src.height}")
        print(f"Bands: {src.count}")
        print(f"CRS: {src.crs}")

        height = src.height
        width = src.width
        num_bands = src.count
        nodata_value = src.nodata

        print(f"\nTarget patches: {target_patches}")

        saved_patches = []
        attempts = 0
        max_attempts = target_patches * 3
        rejected_count = 0

        valid_y_max = height - patch_size
        valid_x_max = width - patch_size

        if valid_y_max <= 0 or valid_x_max <= 0:
            print("❌ Image too small")
            return []

        random.seed(42)

        with tqdm(total=target_patches, desc="Sampling patches") as pbar:

            while len(saved_patches) < target_patches and attempts < max_attempts:
                attempts += 1

                # Random location
                y = random.randint(0, valid_y_max)
                x = random.randint(0, valid_x_max)

                window = rasterio.windows.Window(x, y, patch_size, patch_size)

                try:
                    # Read patch
                    patch_data = src.read(window=window)  # (bands, H, W)
                    patch_data = np.transpose(patch_data, (1, 2, 0))  # (H, W, bands)

                    if patch_data.shape != (patch_size, patch_size, num_bands):
                        continue

                    # Handle NaN
                    patch_data = np.nan_to_num(patch_data, nan=0.0, posinf=0.0, neginf=0.0)

                    # NoData mask (SAME AS TRAINING)
                    if nodata_value is not None:
                        nodata_mask = (patch_data == nodata_value).any(axis=2)
                        nodata_mask |= (np.abs(patch_data) > 1e10).any(axis=2)
                    else:
                        nodata_mask = (np.abs(patch_data) > 1e10).any(axis=2)

                    # Skip if >30% NoData (SAME AS TRAINING)
                    nodata_pct = nodata_mask.sum() / (patch_size ** 2)
                    if nodata_pct > max_nodata_pct:
                        rejected_count += 1
                        continue

                    # Replace NoData with 0
                    img_patch = patch_data.copy()
                    for band_idx in range(img_patch.shape[2]):
                        img_patch[:, :, band_idx][nodata_mask] = 0

                    # Per-patch normalization (EXACT MATCH TO TRAINING)
                    img_patch_norm = np.zeros_like(img_patch, dtype=np.float32)

                    for band_idx in range(img_patch.shape[2]):
                        band_data = img_patch[:, :, band_idx]
                        valid_mask = ~nodata_mask
                        valid_pixels = band_data[valid_mask]

                        if len(valid_pixels) > 10:
                            p2, p98 = np.percentile(valid_pixels, [2, 98])

                            if p98 > p2:
                                normalized = (band_data - p2) / (p98 - p2)
                                img_patch_norm[:, :, band_idx] = np.clip(normalized, 0, 1)
                            else:
                                img_patch_norm[:, :, band_idx] = 0.5
                        else:
                            img_patch_norm[:, :, band_idx] = 0.5

                    # Quality checks (EXACT MATCH TO TRAINING)
                    has_nan = np.isnan(img_patch_norm).any()
                    has_inf = np.isinf(img_patch_norm).any()
                    out_of_range = (img_patch_norm < 0).any() or (img_patch_norm > 1).any()
                    all_zeros = (img_patch_norm == 0).all()
                    all_same = np.allclose(img_patch_norm, img_patch_norm.flat[0])

                    if has_nan or has_inf or out_of_range or all_zeros or all_same:
                        rejected_count += 1
                        continue

                    # Save IMAGE
                    patch_id = len(saved_patches)
                    image_filename = f"negative_patch_{patch_id:04d}.npy"
                    image_path = images_dir / image_filename
                    np.save(image_path, img_patch_norm.astype(np.float32))

                    # Save MASK (all zeros)
                    mask = np.zeros((patch_size, patch_size), dtype=np.uint8)
                    mask_path = masks_dir / image_filename
                    np.save(mask_path, mask)

                    saved_patches.append({
                        'image': image_path,
                        'mask': mask_path
                    })

                    pbar.update(1)

                except Exception as e:
                    rejected_count += 1
                    continue

        print(f"\n✅ Extracted {len(saved_patches)} patches")
        print(f"   Rejected: {rejected_count}")
        print(f"   Success rate: {100 * len(saved_patches) / attempts:.1f}%")

        # Verify sample
        if saved_patches:
            sample_img = np.load(saved_patches[0]['image'])
            sample_mask = np.load(saved_patches[0]['mask'])

            print(f"\nVerification:")
            print(f"  Image shape: {sample_img.shape}")
            print(f"  Image range: [{sample_img.min():.4f}, {sample_img.max():.4f}]")
            print(f"  Mask shape: {sample_mask.shape}")
            print(f"  Mask sum: {sample_mask.sum()}")

        return saved_patches


def visualize_patch(image_path, mask_path):
    """Visualize patch"""

    patch = np.load(image_path)
    mask = np.load(mask_path)

    fig, axes = plt.subplots(2, 3, figsize=(15, 10))
    fig.suptitle(f"{Path(image_path).name} - {patch.shape[2]} bands", fontsize=14, fontweight='bold')

    # RGB (B4, B3, B2)
    if patch.shape[2] >= 3:
        rgb = patch[:, :, [2, 1, 0]]
        axes[0, 0].imshow(np.clip(rgb, 0, 1))
    axes[0, 0].set_title('RGB')
    axes[0, 0].axis('off')

    # NIR (B8)
    if patch.shape[2] >= 4:
        axes[0, 1].imshow(patch[:, :, 3], cmap='gray')
        axes[0, 1].set_title('NIR')
    axes[0, 1].axis('off')

    # SWIR (B11)
    if patch.shape[2] >= 5:
        axes[0, 2].imshow(patch[:, :, 4], cmap='hot')
        axes[0, 2].set_title('SWIR')
    axes[0, 2].axis('off')

    # Custom indices (if present)
    # NDMoI, BSI, GCVI, EVI are typically last 4 bands
    if patch.shape[2] > 10:
        axes[1, 0].imshow(patch[:, :, -4], cmap='RdYlGn')
        axes[1, 0].set_title('NDMoI')
        axes[1, 0].axis('off')

        axes[1, 1].imshow(patch[:, :, -3], cmap='RdYlBu_r')
        axes[1, 1].set_title('BSI')
        axes[1, 1].axis('off')

    # Mask
    axes[1, 2].imshow(mask, cmap='gray', vmin=0, vmax=1)
    axes[1, 2].set_title('Mask (zeros)')
    axes[1, 2].axis('off')

    plt.tight_layout()
    save_path = Path(image_path).parent.parent / f"{Path(image_path).stem}_preview.png"
    plt.savefig(save_path, dpi=150, bbox_inches='tight')
    print(f"Preview: {save_path.name}")
    plt.close()


# ===================================================================
# MAIN
# ===================================================================

if __name__ == "__main__":

    # YOUR PATHS
    download_dir = Path(r'C:\Users\mcnob\Documents\Ashesi A\Capstone\Mining_Negatives_Training_HLS\qwww')
    output_dir = Path(r'C:\Users\mcnob\Documents\Ashesi A\Capstone\negative_training_data_HLS')

    # Find the merged TIF
    tif_files = list(download_dir.glob('*.tif'))

    if len(tif_files) == 0:
        print("❌ No TIF files found!")
        print(f"   Looking in: {download_dir}")
    else:
        print("=" * 60)
        print("NEGATIVE PATCH EXTRACTION")
        print("=" * 60)
        print(f"Found {len(tif_files)} TIF file(s)")

        all_patches = []

        for tif_file in tif_files:
            print(f"\nProcessing: {tif_file.name}")

            patches = sample_patches_from_tif(
                tif_file,
                output_dir,
                target_patches=500,
                patch_size=256
            )

            all_patches.extend(patches)

        print("\n" + "=" * 60)
        print("EXTRACTION SUMMARY")
        print("=" * 60)
        print(f"Total patches: {len(all_patches)}")

        if all_patches:
            # Visualize samples
            for i in [0, len(all_patches) // 2, -1]:
                visualize_patch(all_patches[i]['image'], all_patches[i]['mask'])

            # Save metadata
            sample = np.load(all_patches[0]['image'])

            metadata = {
                'source_files': [str(f) for f in tif_files],
                'total_patches': len(all_patches),
                'patch_size': 256,
                'bands': sample.shape[2],
                'label': 'negative',
                'normalization': 'per-patch percentile (2nd-98th)',
                'output_structure': {
                    'images': str(output_dir / 'images'),
                    'masks': str(output_dir / 'masks')
                }
            }

            with open(output_dir / 'negative_metadata.json', 'w') as f:
                json.dump(metadata, f, indent=2)

            print(f"\n✅ SUCCESS!")
            print(f"   Output: {output_dir}")
            print(f"   Images: {output_dir / 'images'}")
            print(f"   Masks: {output_dir / 'masks'}")
            print(f"   Bands: {sample.shape[2]}")
            print(f"\nNext: Upload to Google Drive for fine-tuning!")