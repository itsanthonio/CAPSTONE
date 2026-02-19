import rasterio
import numpy as np
from pathlib import Path
from tqdm import tqdm
import json


class BalancedOpticalExtractor:
    def __init__(self, patch_size=256, stride=128, output_dir='./optical_training_data'):
        self.patch_size = patch_size
        self.stride = stride
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)

    def verify_patch_quality(self, patch):
        """Strict quality checks before saving"""
        checks = {
            'has_nan': np.isnan(patch).any(),
            'has_inf': np.isinf(patch).any(),
            'out_of_range': (patch < 0).any() or (patch > 1).any(),
            'all_zeros': (patch == 0).all(),
            'all_same': np.allclose(patch, patch.flat[0]) if patch.size > 0 else True
        }

        if any(checks.values()):
            return False, checks
        return True, checks

    def extract_patches_from_pair(self, image_path, mask_path, year, target_per_year):
        """Extract balanced patches from image-mask pair"""

        print(f"\nProcessing {year} data...")

        with rasterio.open(image_path) as img_src, rasterio.open(mask_path) as mask_src:
            image = img_src.read()  # (bands, H, W)
            mask = mask_src.read(1)  # (H, W)
            nodata_value = img_src.nodata

        # Transpose to (H, W, bands)
        image = np.transpose(image, (1, 2, 0))

        print(f"  Image shape: {image.shape}")
        print(f"  NoData value: {nodata_value}")
        print(f"  Mining pixels: {(mask == 1).sum()}")

        # Create NoData mask - handle multiple possible NoData values
        if nodata_value is not None:
            # Check for exact match and extreme values
            nodata_mask = (image == nodata_value).any(axis=2)
            nodata_mask |= (np.abs(image) > 1e10).any(axis=2)  # Catch extreme values
        else:
            # If no NoData specified, look for extreme values
            nodata_mask = (np.abs(image) > 1e10).any(axis=2)

        print(f"  NoData pixels: {nodata_mask.sum()} ({100 * nodata_mask.sum() / nodata_mask.size:.2f}%)")

        patches = []
        labels = []

        target_positive = target_per_year // 2
        target_negative = target_per_year // 2

        # ============================================================
        # EXTRACT POSITIVE PATCHES (contain mining)
        # ============================================================
        print(f"  Extracting {target_positive} positive patches...")

        mining_pixels = np.argwhere(mask == 1)
        rejected_count = 0

        if len(mining_pixels) > 0:
            # Sample mining pixels - oversample to account for rejections
            sample_rate = max(1, len(mining_pixels) // (target_positive * 3))
            sampled_mining = mining_pixels[::sample_rate]

            for center_y, center_x in tqdm(sampled_mining, desc="Positive patches"):
                if len(patches) >= target_positive:
                    break

                # Extract patch centered on mining pixel
                y_start = center_y - self.patch_size // 2
                y_end = y_start + self.patch_size
                x_start = center_x - self.patch_size // 2
                x_end = x_start + self.patch_size

                # Check bounds
                if y_start < 0 or y_end > image.shape[0] or x_start < 0 or x_end > image.shape[1]:
                    continue

                # Extract patch
                img_patch = image[y_start:y_end, x_start:x_end].copy()
                mask_patch = mask[y_start:y_end, x_start:x_end].copy()
                nodata_patch = nodata_mask[y_start:y_end, x_start:x_end]

                # Skip if >30% NoData
                nodata_percentage = nodata_patch.sum() / (self.patch_size ** 2)
                if nodata_percentage > 0.3:
                    rejected_count += 1
                    continue

                # Replace NoData with 0 (will be handled in normalization)
                for band in range(img_patch.shape[2]):
                    img_patch[:, :, band][nodata_patch] = 0

                # Normalize using percentile stretch on VALID pixels only
                img_patch_norm = np.zeros_like(img_patch, dtype=np.float32)

                for band in range(img_patch.shape[2]):
                    band_data = img_patch[:, :, band]
                    valid_mask = ~nodata_patch
                    valid_pixels = band_data[valid_mask]

                    if len(valid_pixels) > 10:  # Need enough pixels
                        p2, p98 = np.percentile(valid_pixels, [2, 98])

                        if p98 > p2:  # Avoid division by zero
                            # Normalize ALL pixels using valid stats
                            normalized = (band_data - p2) / (p98 - p2)
                            img_patch_norm[:, :, band] = np.clip(normalized, 0, 1)
                        else:
                            img_patch_norm[:, :, band] = 0.5
                    else:
                        img_patch_norm[:, :, band] = 0.5

                # VERIFY QUALITY
                is_valid, checks = self.verify_patch_quality(img_patch_norm)
                if not is_valid:
                    rejected_count += 1
                    continue

                patches.append(img_patch_norm)
                labels.append(mask_patch.astype(np.uint8))

        print(f"  Extracted {len(patches)} positive patches (rejected {rejected_count})")

        # ============================================================
        # EXTRACT NEGATIVE PATCHES (no mining)
        # ============================================================
        print(f"  Extracting {target_negative} negative patches...")

        negative_count = 0
        rejected_count = 0
        attempts = 0
        max_attempts = target_negative * 50  # Increased attempts

        # Random sampling for negatives
        np.random.seed(42)

        pbar = tqdm(total=target_negative, desc="Negative patches")

        while negative_count < target_negative and attempts < max_attempts:
            attempts += 1

            # Random location
            y = np.random.randint(0, image.shape[0] - self.patch_size)
            x = np.random.randint(0, image.shape[1] - self.patch_size)

            # Extract patch - FIXED: x:x+self.patch_size (not x:y+self.patch_size)
            img_patch = image[y:y + self.patch_size, x:x + self.patch_size].copy()
            mask_patch = mask[y:y + self.patch_size, x:x + self.patch_size].copy()
            nodata_patch = nodata_mask[y:y + self.patch_size, x:x + self.patch_size]

            # Skip if contains mining
            if (mask_patch == 1).any():
                continue

            # Skip if >30% NoData
            nodata_percentage = nodata_patch.sum() / (self.patch_size ** 2)
            if nodata_percentage > 0.3:
                rejected_count += 1
                continue

            # Replace NoData with 0
            for band in range(img_patch.shape[2]):
                img_patch[:, :, band][nodata_patch] = 0

            # Normalize
            img_patch_norm = np.zeros_like(img_patch, dtype=np.float32)

            for band in range(img_patch.shape[2]):
                band_data = img_patch[:, :, band]
                valid_mask = ~nodata_patch
                valid_pixels = band_data[valid_mask]

                if len(valid_pixels) > 10:
                    p2, p98 = np.percentile(valid_pixels, [2, 98])

                    if p98 > p2:
                        normalized = (band_data - p2) / (p98 - p2)
                        img_patch_norm[:, :, band] = np.clip(normalized, 0, 1)
                    else:
                        img_patch_norm[:, :, band] = 0.5
                else:
                    img_patch_norm[:, :, band] = 0.5

            # VERIFY QUALITY
            is_valid, checks = self.verify_patch_quality(img_patch_norm)
            if not is_valid:
                rejected_count += 1
                continue

            patches.append(img_patch_norm)
            labels.append(mask_patch.astype(np.uint8))
            negative_count += 1
            pbar.update(1)

        pbar.close()

        print(f"  Extracted {negative_count} negative patches (rejected {rejected_count}, attempted {attempts})")

        if negative_count < target_negative:
            print(
                f"  WARNING: Only got {negative_count}/{target_negative} negative patches after {max_attempts} attempts")

        return patches, labels

    def extract_and_split_patches(self, data_config, target_total=10000,
                                  train_ratio=0.7, val_ratio=0.15, test_ratio=0.15):
        """Extract patches from multiple years and split into train/val/test"""

        all_patches = []
        all_labels = []

        target_per_year = target_total // len(data_config)

        print(f"\n{'=' * 60}")
        print(f"EXTRACTION PLAN")
        print(f"{'=' * 60}")
        print(f"Target total patches: {target_total}")
        print(f"Number of years: {len(data_config)}")
        print(f"Target per year: {target_per_year}")
        print(f"  Positive per year: {target_per_year // 2}")
        print(f"  Negative per year: {target_per_year // 2}")
        print(f"{'=' * 60}")

        # Extract from each year
        for config in data_config:
            patches, labels = self.extract_patches_from_pair(
                config['image_path'],
                config['mask_path'],
                config['year'],
                target_per_year
            )
            all_patches.extend(patches)
            all_labels.extend(labels)

        print(f"\n{'=' * 60}")
        print(f"EXTRACTION SUMMARY")
        print(f"{'=' * 60}")
        print(f"TOTAL EXTRACTED: {len(all_patches)} patches")
        print(f"Target was: {target_total} patches")
        print(f"Success rate: {100 * len(all_patches) / target_total:.1f}%")

        # Count positive/negative
        positive_count = sum(1 for label in all_labels if (label == 1).any())
        negative_count = len(all_labels) - positive_count
        print(f"Positive patches: {positive_count}")
        print(f"Negative patches: {negative_count}")
        print(f"Balance: {100 * positive_count / len(all_labels):.1f}% positive")

        # Final verification
        print(f"\nVerifying patch quality...")
        nan_count = sum(1 for p in all_patches if np.isnan(p).any())
        inf_count = sum(1 for p in all_patches if np.isinf(p).any())

        print(f"Patches with NaN: {nan_count}")
        print(f"Patches with Inf: {inf_count}")

        if nan_count > 0 or inf_count > 0:
            print("❌ ERROR: Some patches still contain NaN/Inf!")
            return None

        print("✅ All patches verified clean!")

        # Convert to arrays
        print(f"\nConverting to numpy arrays...")
        all_patches = np.array(all_patches, dtype=np.float32)
        all_labels = np.array(all_labels, dtype=np.uint8)

        print(f"Array shapes:")
        print(f"  Images: {all_patches.shape}")
        print(f"  Labels: {all_labels.shape}")

        # Split into train/val/test
        print(f"\nSplitting into train/val/test...")
        train_size = int(len(all_patches) * train_ratio)
        val_size = int(len(all_patches) * val_ratio)

        indices = np.random.permutation(len(all_patches))
        train_idx = indices[:train_size]
        val_idx = indices[train_size:train_size + val_size]
        test_idx = indices[train_size + val_size:]

        print(f"Train: {len(train_idx)} patches ({100 * len(train_idx) / len(all_patches):.1f}%)")
        print(f"Val:   {len(val_idx)} patches ({100 * len(val_idx) / len(all_patches):.1f}%)")
        print(f"Test:  {len(test_idx)} patches ({100 * len(test_idx) / len(all_patches):.1f}%)")

        splits = {
            'train': (train_idx, len(train_idx)),
            'val': (val_idx, len(val_idx)),
            'test': (test_idx, len(test_idx))
        }

        # Save patches
        for split_name, (idx, count) in splits.items():
            print(f"\nSaving {split_name}: {count} patches")

            split_dir = self.output_dir / split_name
            (split_dir / 'images').mkdir(parents=True, exist_ok=True)
            (split_dir / 'masks').mkdir(parents=True, exist_ok=True)

            for i, patch_idx in enumerate(tqdm(idx, desc=f"Saving {split_name}")):
                np.save(
                    split_dir / 'images' / f'patch_{i:05d}.npy',
                    all_patches[patch_idx]
                )
                np.save(
                    split_dir / 'masks' / f'mask_{i:05d}.npy',
                    all_labels[patch_idx]
                )

        # Save metadata
        metadata = {
            'total_patches': len(all_patches),
            'positive_patches': int(positive_count),
            'negative_patches': int(negative_count),
            'train': len(train_idx),
            'val': len(val_idx),
            'test': len(test_idx),
            'patch_size': self.patch_size,
            'bands': all_patches.shape[-1],
            'train_ratio': train_ratio,
            'val_ratio': val_ratio,
            'test_ratio': test_ratio
        }

        with open(self.output_dir / 'dataset_metadata.json', 'w') as f:
            json.dump(metadata, f, indent=2)

        print(f"\n{'=' * 60}")
        print("DATASET CREATION COMPLETE")
        print(f"{'=' * 60}")
        print(json.dumps(metadata, indent=2))
        print(f"\nDataset saved to: {self.output_dir}")

        return metadata


# ===================================================================
# FULL EXTRACTION
# ===================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("FULL EXTRACTION MODE")
    print("=" * 60)

    extractor = BalancedOpticalExtractor(
        patch_size=256,
        stride=128,
        output_dir='./optical_training_data'
    )

    # YOUR PATHS
    data_config = [
        {
            'image_path': 'C:\\Users\\mcnob\\Documents\\Ashesi A\\Capstone\\HLS_Dataset\\Merged Files and Masks\\2021_Final.tif',
            'mask_path': 'C:\\Users\\mcnob\\Documents\\Ashesi A\\Capstone\\HLS_Dataset\\Merged Files and Masks\\reproj_2021.tif',
            'year': 2021
        },
        {
            'image_path': 'C:\\Users\\mcnob\\Documents\\Ashesi A\\Capstone\\HLS_Dataset\\Merged Files and Masks\\2024_Final.tif',
            'mask_path': 'C:\\Users\\mcnob\\Documents\\Ashesi A\\Capstone\\HLS_Dataset\\Merged Files and Masks\\reproj_2024.tif',
            'year': 2024
        }
    ]

    # INCREASED TARGET to compensate for rejection rate
    metadata = extractor.extract_and_split_patches(
        data_config=data_config,
        target_total=10000,
        train_ratio=0.7,
        val_ratio=0.15,
        test_ratio=0.15
    )

    # ===================================================================
    # VERIFICATION PHASE
    # ===================================================================
    if metadata is not None:
        print("\n" + "=" * 60)
        print("FINAL VERIFICATION")
        print("=" * 60)

        final_dir = Path('./optical_training_data')
        all_good = True

        for split in ['train', 'val', 'test']:
            img_dir = final_dir / split / 'images'
            mask_dir = final_dir / split / 'masks'

            if not img_dir.exists():
                continue

            img_files = list(img_dir.glob('*.npy'))
            mask_files = list(mask_dir.glob('*.npy'))

            print(f"\n{split.upper()}:")
            print(f"  Images: {len(img_files)}")
            print(f"  Masks: {len(mask_files)}")

            if len(img_files) != len(mask_files):
                print(f"  ❌ MISMATCH: {len(img_files)} images vs {len(mask_files)} masks")
                all_good = False
                continue

            # Check sample
            sample_files = img_files[:5] + img_files[-5:] if len(img_files) > 10 else img_files

            for img_file in sample_files:
                img = np.load(img_file)

                if np.isnan(img).any():
                    print(f"  ❌ NaN in: {img_file.name}")
                    all_good = False
                elif np.isinf(img).any():
                    print(f"  ❌ Inf in: {img_file.name}")
                    all_good = False
                elif img.min() < -0.01 or img.max() > 1.01:
                    print(f"  ❌ Range in: {img_file.name} [{img.min():.4f}, {img.max():.4f}]")
                    all_good = False

        print("\n" + "=" * 60)
        if all_good:
            print("✅✅✅ DATASET READY FOR TRAINING! ✅✅✅")
            print("=" * 60)
            print("\nNext steps:")
            print("1. Upload 'optical_training_data' folder to Google Drive")
            print("2. Run training code in Colab")
            print(f"3. Expected results: 60-75% IoU with {metadata['total_patches']} patches")
        else:
            print("❌❌❌ VERIFICATION FAILED ❌❌❌")
            print("=" * 60)