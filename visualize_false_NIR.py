# ===================================================================
# VISUALIZE 6-BAND TRAINING DATA - LOCAL VERSION
# Display False Color (R-G-NIR) composites from training/val/test sets
# Shows both positive (mining) and negative (non-mining) samples
# Run this locally on your computer
# ===================================================================

import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
import random


# ===================================================================
# Configuration
# ===================================================================

class Config:
    # Data paths - CHANGE THESE TO YOUR LOCAL PATHS
    DATA_DIR = r"C:\Users\mcnob\Documents\Ashesi A\Capstone\optical_training_data"  # Change this!
    NEGATIVE_DATA_DIR = r"C:\Users\mcnob\Documents\Ashesi A\Capstone\negative_training_data_HLS"  # Change this!

    # Band selection - Must match training
    SELECTED_BANDS = [2, 3, 7, 10, 11, 19]  # GREEN, RED, NIR, SWIR1, SWIR2, BSI

    # How many samples to display
    NUM_SAMPLES_PER_SPLIT = 50  # Show 12 samples per split (train/val/test)
    NUM_NEGATIVE_SAMPLES = 50  # Show 12 negative samples

    # Display settings
    GRID_COLS = 3  # 4 images per row

    # Output directory for saved images
    OUTPUT_DIR = r"C:\Users\mcnob\Documents\Ashesi A\Capstone\visualizations-nir"  # Change this!


config = Config()

print("Configuration:")
print(f"  Data directory: {config.DATA_DIR}")
print(f"  Negative data directory: {config.NEGATIVE_DATA_DIR}")
print(f"  Output directory: {config.OUTPUT_DIR}")
print(f"  Selected bands: {config.SELECTED_BANDS}")
print(f"  Samples per split: {config.NUM_SAMPLES_PER_SPLIT}")
print(f"  Negative samples: {config.NUM_NEGATIVE_SAMPLES}")

# Create output directory
Path(config.OUTPUT_DIR).mkdir(exist_ok=True, parents=True)


# ===================================================================
# Helper Functions
# ===================================================================

def load_and_select_bands(npy_path, selected_bands):
    """
    Load .npy file and select only the specified bands

    Args:
        npy_path: Path to .npy file (256, 256, 22)
        selected_bands: List of band indices to select

    Returns:
        numpy array of shape (256, 256, 6)
    """
    patch = np.load(npy_path)  # Shape: (256, 256, 22)

    # Check shape
    if patch.shape[2] < max(selected_bands) + 1:
        print(f"⚠️ Warning: {npy_path.name} has only {patch.shape[2]} bands")
        return None

    # Select bands
    patch_selected = patch[:, :, selected_bands]  # Shape: (256, 256, 6)

    return patch_selected


def normalize_percentile(patch):
    """
    Normalize using 2nd-98th percentile (matches training)

    Args:
        patch: numpy array (256, 256, 6)

    Returns:
        normalized patch (256, 256, 6) with values in [0, 1]
    """
    patch_normalized = np.zeros_like(patch, dtype=np.float32)

    for band in range(patch.shape[2]):
        band_data = patch[:, :, band]

        # Match training normalization: only exclude inf/nan, KEEP zeros
        valid_mask = np.isfinite(band_data)
        valid_pixels = band_data[valid_mask]

        if len(valid_pixels) > 10:
            p2, p98 = np.percentile(valid_pixels, [2, 98])

            if p98 > p2:
                normalized = (band_data - p2) / (p98 - p2)
                patch_normalized[:, :, band] = np.clip(normalized, 0, 1)
            else:
                patch_normalized[:, :, band] = 0.5
        else:
            patch_normalized[:, :, band] = 0.5

    return patch_normalized


def create_false_color_rgb(patch_6bands):
    """
    Create false color composite from 6-band patch

    Band order in 6-band patch: [GREEN, RED, NIR, SWIR1, SWIR2, BSI]
    For false color: R=RED, G=GREEN, B=NIR

    Args:
        patch_6bands: (256, 256, 6) array

    Returns:
        (256, 256, 3) RGB array ready for display
    """
    # Extract bands
    green = patch_6bands[:, :, 0]  # GREEN
    red = patch_6bands[:, :, 1]  # RED
    nir = patch_6bands[:, :, 2]  # NIR

    # Stack as RGB: Red channel = RED, Green channel = GREEN, Blue channel = NIR
    rgb = np.stack([red, green, nir], axis=2)

    # Clip to valid range
    rgb = np.clip(rgb, 0, 1)

    return rgb


def visualize_samples(image_paths, mask_paths, title, split_name):
    """
    Visualize multiple samples in a grid

    Args:
        image_paths: List of paths to image .npy files
        mask_paths: List of paths to mask .npy files
        title: Title for the figure
        split_name: Name of split (for saving)
    """
    n_samples = len(image_paths)

    if n_samples == 0:
        print(f"⚠️ No samples found for {split_name}")
        return

    # Calculate grid dimensions
    cols = config.GRID_COLS
    rows = (n_samples + cols - 1) // cols

    # Create figure
    fig, axes = plt.subplots(rows, cols * 2, figsize=(cols * 5, rows * 2.5))

    # Handle single row case
    if rows == 1:
        axes = axes.reshape(1, -1)

    print(f"\nProcessing {n_samples} samples for {split_name}...")

    for idx in range(n_samples):
        # Load and process image
        patch_22bands = load_and_select_bands(image_paths[idx], config.SELECTED_BANDS)

        if patch_22bands is None:
            continue

        # Normalize
        patch_normalized = normalize_percentile(patch_22bands)

        # Create false color RGB
        rgb = create_false_color_rgb(patch_normalized)

        # Load mask
        mask = np.load(mask_paths[idx])

        # Calculate row and column
        row = idx // cols
        col_image = (idx % cols) * 2
        col_mask = col_image + 1

        # Plot false color image
        axes[row, col_image].imshow(rgb)
        axes[row, col_image].set_title(f'{image_paths[idx].stem}', fontsize=8)
        axes[row, col_image].axis('off')

        # Plot mask
        axes[row, col_mask].imshow(mask, cmap='gray', vmin=0, vmax=1)
        axes[row, col_mask].set_title('Mask', fontsize=8)
        axes[row, col_mask].axis('off')

    # Hide unused subplots
    total_plots = rows * cols * 2
    for idx in range(n_samples * 2, total_plots):
        row = idx // (cols * 2)
        col = idx % (cols * 2)
        axes[row, col].axis('off')

    # Add main title
    fig.suptitle(title, fontsize=16, fontweight='bold')

    plt.tight_layout(rect=[0, 0.03, 1, 0.97])

    # Save
    save_path = Path(config.OUTPUT_DIR) / f'visualization_{split_name}.png'
    plt.savefig(save_path, dpi=150, bbox_inches='tight')
    print(f"✅ Saved: {save_path}")

    plt.show()


# ===================================================================
# Visualize TRAINING Data
# ===================================================================

print("\n" + "=" * 60)
print("VISUALIZING TRAINING DATA")
print("=" * 60)

# Get training images
train_img_dir = Path(config.DATA_DIR) / 'train' / 'images'
train_mask_dir = Path(config.DATA_DIR) / 'train' / 'masks'

print(f"\n🔍 DEBUG: Looking in: {train_img_dir}")
print(f"🔍 DEBUG: Directory exists: {train_img_dir.exists()}")

if not train_img_dir.exists():
    print(f"⚠️ Training directory not found: {train_img_dir}")
else:
    # Get ALL image files - NO FILTERING
    all_train_images = list(train_img_dir.glob('*.npy'))

    print(f"🔍 DEBUG: Found {len(all_train_images)} .npy files in images folder")
    if len(all_train_images) > 0:
        print(f"🔍 DEBUG: First 5 filenames:")
        for i, f in enumerate(all_train_images[:5]):
            print(f"    {i + 1}. {f.name}")

    # Match with masks by NUMBER, not exact filename
    train_images = []
    train_masks = []

    for img_path in all_train_images:
        # Extract number from patch_XXX.npy
        img_name = img_path.stem  # e.g., "patch_001" or "negative_patch_001"

        # Try to extract the number
        if 'patch_' in img_name:
            # Get everything after the last underscore
            number = img_name.split('_')[-1]  # e.g., "001"
            mask_name = f"mask_{number}.npy"
            mask_path = train_mask_dir / mask_name

            print(f"🔍 DEBUG: {img_path.name} -> looking for {mask_name} -> exists: {mask_path.exists()}")

            if mask_path.exists():
                train_images.append(img_path)
                train_masks.append(mask_path)

    print(f"\n✅ Found {len(train_images)} training samples with matching masks")

    # Randomly sample
    if len(train_images) > config.NUM_SAMPLES_PER_SPLIT:
        indices = random.sample(range(len(train_images)), config.NUM_SAMPLES_PER_SPLIT)
        sample_images = [train_images[i] for i in indices]
        sample_masks = [train_masks[i] for i in indices]
    else:
        sample_images = train_images
        sample_masks = train_masks

    # Visualize
    visualize_samples(
        sample_images,
        sample_masks,
        f'TRAINING SET (False Color: R-G-NIR)\nShowing {len(sample_images)} of {len(train_images)} samples',
        'train'
    )

# ===================================================================
# Visualize VALIDATION Data
# ===================================================================

print("\n" + "=" * 60)
print("VISUALIZING VALIDATION DATA")
print("=" * 60)

# Get validation images
val_img_dir = Path(config.DATA_DIR) / 'val' / 'images'
val_mask_dir = Path(config.DATA_DIR) / 'val' / 'masks'

print(f"\n🔍 DEBUG: Looking in: {val_img_dir}")
print(f"🔍 DEBUG: Directory exists: {val_img_dir.exists()}")

if not val_img_dir.exists():
    print(f"⚠️ Validation directory not found: {val_img_dir}")
else:
    # Get ALL image files - NO FILTERING
    all_val_images = list(val_img_dir.glob('*.npy'))

    print(f"🔍 DEBUG: Found {len(all_val_images)} .npy files in images folder")
    if len(all_val_images) > 0:
        print(f"🔍 DEBUG: First 5 filenames:")
        for i, f in enumerate(all_val_images[:5]):
            print(f"    {i + 1}. {f.name}")

    val_images = []
    val_masks = []

    for img_path in all_val_images:
        # Extract number from patch_XXX.npy
        img_name = img_path.stem

        if 'patch_' in img_name:
            number = img_name.split('_')[-1]
            mask_name = f"mask_{number}.npy"
            mask_path = val_mask_dir / mask_name

            if mask_path.exists():
                val_images.append(img_path)
                val_masks.append(mask_path)

    print(f"\n✅ Found {len(val_images)} validation samples with matching masks")

    # Randomly sample
    if len(val_images) > config.NUM_SAMPLES_PER_SPLIT:
        indices = random.sample(range(len(val_images)), config.NUM_SAMPLES_PER_SPLIT)
        sample_images = [val_images[i] for i in indices]
        sample_masks = [val_masks[i] for i in indices]
    else:
        sample_images = val_images
        sample_masks = val_masks

    # Visualize
    visualize_samples(
        sample_images,
        sample_masks,
        f'VALIDATION SET (False Color: R-G-NIR)\nShowing {len(sample_images)} of {len(val_images)} samples',
        'validation'
    )

# ===================================================================
# Visualize TEST Data
# ===================================================================

print("\n" + "=" * 60)
print("VISUALIZING TEST DATA")
print("=" * 60)

# Get test images
test_img_dir = Path(config.DATA_DIR) / 'test' / 'images'
test_mask_dir = Path(config.DATA_DIR) / 'test' / 'masks'

print(f"\n🔍 DEBUG: Looking in: {test_img_dir}")
print(f"🔍 DEBUG: Directory exists: {test_img_dir.exists()}")

if not test_img_dir.exists():
    print(f"⚠️ Test directory not found: {test_img_dir}")
else:
    # Get ALL image files - NO FILTERING
    all_test_images = list(test_img_dir.glob('*.npy'))

    print(f"🔍 DEBUG: Found {len(all_test_images)} .npy files in images folder")
    if len(all_test_images) > 0:
        print(f"🔍 DEBUG: First 5 filenames:")
        for i, f in enumerate(all_test_images[:5]):
            print(f"    {i + 1}. {f.name}")

    test_images = []
    test_masks = []

    for img_path in all_test_images:
        # Extract number from patch_XXX.npy
        img_name = img_path.stem

        if 'patch_' in img_name:
            number = img_name.split('_')[-1]
            mask_name = f"mask_{number}.npy"
            mask_path = test_mask_dir / mask_name

            if mask_path.exists():
                test_images.append(img_path)
                test_masks.append(mask_path)

    print(f"\n✅ Found {len(test_images)} test samples with matching masks")

    # Randomly sample
    if len(test_images) > config.NUM_SAMPLES_PER_SPLIT:
        indices = random.sample(range(len(test_images)), config.NUM_SAMPLES_PER_SPLIT)
        sample_images = [test_images[i] for i in indices]
        sample_masks = [test_masks[i] for i in indices]
    else:
        sample_images = test_images
        sample_masks = test_masks

    # Visualize
    visualize_samples(
        sample_images,
        sample_masks,
        f'TEST SET (False Color: R-G-NIR)\nShowing {len(sample_images)} of {len(test_images)} samples',
        'test'
    )

# ===================================================================
# Visualize NEGATIVE Training Data
# ===================================================================

print("\n" + "=" * 60)
print("VISUALIZING NEGATIVE TRAINING DATA")
print("=" * 60)

# Get negative training images
neg_img_dir = Path(config.NEGATIVE_DATA_DIR) / 'images'
neg_mask_dir = Path(config.NEGATIVE_DATA_DIR) / 'masks'

if not neg_img_dir.exists():
    print(f"⚠️ Negative data directory not found: {neg_img_dir}")
else:
    # Get ALL image files - NO FILTERING
    all_neg_images = list(neg_img_dir.glob('*.npy'))
    neg_images = []
    neg_masks = []

    for img_path in all_neg_images:
        mask_path = neg_mask_dir / img_path.name
        if mask_path.exists():
            neg_images.append(img_path)
            neg_masks.append(mask_path)

    print(f"\nFound {len(neg_images)} negative samples")

    # Randomly sample
    if len(neg_images) > config.NUM_NEGATIVE_SAMPLES:
        indices = random.sample(range(len(neg_images)), config.NUM_NEGATIVE_SAMPLES)
        sample_images = [neg_images[i] for i in indices]
        sample_masks = [neg_masks[i] for i in indices]
    else:
        sample_images = neg_images
        sample_masks = neg_masks

    # Visualize
    visualize_samples(
        sample_images,
        sample_masks,
        f'NEGATIVE TRAINING SET (False Color: R-G-NIR)\nForests, Water, Urban - NO Mining\nShowing {len(sample_images)} of {len(neg_images)} samples',
        'negative'
    )

# ===================================================================
# Summary
# ===================================================================

print("\n" + "=" * 60)
print("VISUALIZATION COMPLETE!")
print("=" * 60)
print("\nAll visualizations saved to:")
print(f"  {config.OUTPUT_DIR}")
print("\nFiles created:")
print("  - visualization_train.png")
print("  - visualization_validation.png")
print("  - visualization_test.png")
print("  - visualization_negative.png")
print("\nFalse Color Composite:")
print("  Red channel = RED band (B4)")
print("  Green channel = GREEN band (B3)")
print("  Blue channel = NIR band (B8)")
print("\nHealthy vegetation appears RED (high NIR)")
print("Bare soil/mining appears CYAN/WHITE (low NIR, high visible)")
print("Water appears DARK BLUE/BLACK (low NIR)")