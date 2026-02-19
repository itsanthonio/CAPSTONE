import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
import random


def visualize_patches(data_dir, num_samples=100, save_dir='./visualizations'):
    """
    Visualize random patches from train, val, test splits

    Args:
        data_dir: Path to optical_training_data/
        num_samples: Number of samples to visualize per split
        save_dir: Where to save the visualization images
    """

    data_dir = Path(data_dir)
    save_dir = Path(save_dir)
    save_dir.mkdir(exist_ok=True)

    for split in ['train', 'val', 'test']:
        print(f"\n{'=' * 60}")
        print(f"Visualizing {split.upper()} split")
        print(f"{'=' * 60}")

        img_dir = data_dir / split / 'images'
        mask_dir = data_dir / split / 'masks'

        if not img_dir.exists():
            print(f"  Skipping {split} - directory not found")
            continue

        # Get all files
        img_files = sorted(list(img_dir.glob('*.npy')))
        mask_files = sorted(list(mask_dir.glob('*.npy')))

        print(f"  Found {len(img_files)} images, {len(mask_files)} masks")

        # Random sample
        num_to_show = min(num_samples, len(img_files))
        indices = random.sample(range(len(img_files)), num_to_show)

        # Create grid: 10 columns, as many rows as needed
        cols = 10
        rows = (num_to_show + cols - 1) // cols

        # Create figure for RGB visualization
        fig_rgb, axes_rgb = plt.subplots(rows, cols, figsize=(20, 2 * rows))
        axes_rgb = axes_rgb.flatten() if rows > 1 else [axes_rgb] if cols == 1 else axes_rgb

        # Create figure for mask visualization
        fig_mask, axes_mask = plt.subplots(rows, cols, figsize=(20, 2 * rows))
        axes_mask = axes_mask.flatten() if rows > 1 else [axes_mask] if cols == 1 else axes_mask

        print(f"  Creating visualization grid: {rows} rows x {cols} cols")

        for idx, sample_idx in enumerate(indices):
            # Load image and mask
            img = np.load(img_files[sample_idx])  # (256, 256, 22)
            mask = np.load(mask_files[sample_idx])  # (256, 256)

            # Create RGB composite (assuming bands 2,1,0 are RGB or close to it)
            # If your bands are B2,B3,B4,B8,B11... then use first 3 for RGB-like
            rgb = img[:, :, :3]  # First 3 bands

            # Normalize RGB for display
            rgb_display = np.clip(rgb, 0, 1)

            # Plot RGB
            axes_rgb[idx].imshow(rgb_display)
            axes_rgb[idx].axis('off')

            # Add label if contains mining
            has_mining = (mask == 1).any()
            color = 'red' if has_mining else 'green'
            label = 'Mining' if has_mining else 'No Mining'
            axes_rgb[idx].set_title(label, fontsize=8, color=color)

            # Plot mask
            axes_mask[idx].imshow(mask, cmap='gray', vmin=0, vmax=1)
            axes_mask[idx].axis('off')
            axes_mask[idx].set_title(label, fontsize=8, color=color)

        # Hide unused subplots
        for idx in range(num_to_show, len(axes_rgb)):
            axes_rgb[idx].axis('off')
            axes_mask[idx].axis('off')

        # Save figures
        fig_rgb.suptitle(f'{split.upper()} - RGB Composite (first 3 bands)', fontsize=16, y=0.995)
        fig_rgb.tight_layout()
        fig_rgb.savefig(save_dir / f'{split}_rgb.png', dpi=150, bbox_inches='tight')
        print(f"  ✓ Saved RGB visualization: {save_dir / f'{split}_rgb.png'}")

        fig_mask.suptitle(f'{split.upper()} - Mining Masks', fontsize=16, y=0.995)
        fig_mask.tight_layout()
        fig_mask.savefig(save_dir / f'{split}_masks.png', dpi=150, bbox_inches='tight')
        print(f"  ✓ Saved mask visualization: {save_dir / f'{split}_masks.png'}")

        plt.close('all')

        # Print statistics
        positive_count = sum(1 for idx in indices if (np.load(mask_files[idx]) == 1).any())
        negative_count = num_to_show - positive_count
        print(f"  Statistics:")
        print(f"    Positive (mining): {positive_count} ({100 * positive_count / num_to_show:.1f}%)")
        print(f"    Negative (no mining): {negative_count} ({100 * negative_count / num_to_show:.1f}%)")

    print(f"\n{'=' * 60}")
    print(f"✓ All visualizations saved to: {save_dir}")
    print(f"{'=' * 60}")


def visualize_individual_samples(data_dir, split='train', num_samples=10, save_dir='./visualizations/samples'):
    """
    Create detailed individual patch visualizations with RGB + mask side-by-side

    Args:
        data_dir: Path to optical_training_data/
        split: Which split to visualize
        num_samples: Number of individual samples to create
        save_dir: Where to save individual sample images
    """

    data_dir = Path(data_dir)
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)

    img_dir = data_dir / split / 'images'
    mask_dir = data_dir / split / 'masks'

    print(f"\n{'=' * 60}")
    print(f"Creating individual sample visualizations from {split.upper()}")
    print(f"{'=' * 60}")

    if not img_dir.exists():
        print(f"  Error: {split} directory not found")
        return

    # Get all files
    img_files = sorted(list(img_dir.glob('*.npy')))
    mask_files = sorted(list(mask_dir.glob('*.npy')))

    # Random sample
    num_to_show = min(num_samples, len(img_files))
    indices = random.sample(range(len(img_files)), num_to_show)

    for idx in indices:
        img = np.load(img_files[idx])
        mask = np.load(mask_files[idx])

        # Create RGB composite
        rgb = np.clip(img[:, :, :3], 0, 1)

        # Create figure
        fig, axes = plt.subplots(1, 2, figsize=(10, 5))

        # Plot RGB
        axes[0].imshow(rgb)
        axes[0].set_title('RGB Composite', fontsize=12)
        axes[0].axis('off')

        # Plot mask
        axes[1].imshow(mask, cmap='gray', vmin=0, vmax=1)
        axes[1].set_title('Mining Mask', fontsize=12)
        axes[1].axis('off')

        # Overall title
        has_mining = (mask == 1).any()
        mining_percent = (mask == 1).sum() / mask.size * 100
        label = 'MINING PRESENT' if has_mining else 'NO MINING'
        color = 'red' if has_mining else 'green'

        fig.suptitle(f'{label} - {mining_percent:.1f}% mining pixels',
                     fontsize=14, color=color, weight='bold')

        # Save
        filename = f'{split}_sample_{idx:04d}.png'
        fig.tight_layout()
        fig.savefig(save_dir / filename, dpi=150, bbox_inches='tight')
        plt.close(fig)

    print(f"  ✓ Created {num_to_show} individual samples")
    print(f"  ✓ Saved to: {save_dir}")


def visualize_with_statistics(data_dir, save_dir='./visualizations'):
    """
    Create comprehensive visualization with statistics
    """

    data_dir = Path(data_dir)
    save_dir = Path(save_dir)
    save_dir.mkdir(exist_ok=True)

    print(f"\n{'=' * 60}")
    print(f"DATASET STATISTICS")
    print(f"{'=' * 60}")

    stats = {}

    for split in ['train', 'val', 'test']:
        img_dir = data_dir / split / 'images'
        mask_dir = data_dir / split / 'masks'

        if not img_dir.exists():
            continue

        img_files = list(img_dir.glob('*.npy'))
        mask_files = list(mask_dir.glob('*.npy'))

        # Count positive/negative
        positive = 0
        negative = 0
        total_mining_pixels = 0

        for mask_file in mask_files:
            mask = np.load(mask_file)
            if (mask == 1).any():
                positive += 1
            else:
                negative += 1
            total_mining_pixels += (mask == 1).sum()

        stats[split] = {
            'total': len(img_files),
            'positive': positive,
            'negative': negative,
            'mining_pixels': total_mining_pixels
        }

        print(f"\n{split.upper()}:")
        print(f"  Total patches: {len(img_files)}")
        print(f"  Positive (mining): {positive} ({100 * positive / len(img_files):.1f}%)")
        print(f"  Negative (no mining): {negative} ({100 * negative / len(img_files):.1f}%)")
        print(f"  Total mining pixels: {total_mining_pixels:,}")

    # Create summary visualization
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))

    # Bar chart - patch counts
    splits = list(stats.keys())
    totals = [stats[s]['total'] for s in splits]
    positives = [stats[s]['positive'] for s in splits]
    negatives = [stats[s]['negative'] for s in splits]

    x = np.arange(len(splits))
    width = 0.35

    axes[0].bar(x - width / 2, positives, width, label='Mining', color='red', alpha=0.7)
    axes[0].bar(x + width / 2, negatives, width, label='No Mining', color='green', alpha=0.7)
    axes[0].set_xlabel('Split', fontsize=12)
    axes[0].set_ylabel('Number of Patches', fontsize=12)
    axes[0].set_title('Patch Distribution', fontsize=14)
    axes[0].set_xticks(x)
    axes[0].set_xticklabels([s.upper() for s in splits])
    axes[0].legend()
    axes[0].grid(axis='y', alpha=0.3)

    # Pie chart - overall balance
    total_positive = sum(positives)
    total_negative = sum(negatives)

    axes[1].pie([total_positive, total_negative],
                labels=['Mining', 'No Mining'],
                colors=['red', 'green'],
                autopct='%1.1f%%',
                startangle=90,
                explode=(0.05, 0.05))
    axes[1].set_title('Overall Class Balance', fontsize=14)

    fig.tight_layout()
    fig.savefig(save_dir / 'dataset_statistics.png', dpi=150, bbox_inches='tight')
    plt.close(fig)

    print(f"\n✓ Statistics visualization saved: {save_dir / 'dataset_statistics.png'}")
    print(f"{'=' * 60}")


# ===================================================================
# MAIN EXECUTION
# ===================================================================
if __name__ == "__main__":
    # Set random seed for reproducibility
    random.seed(42)
    np.random.seed(42)

    # Path to your dataset
    data_dir = './optical_training_data'

    # 1. Create grid visualizations (100 samples per split)
    visualize_patches(data_dir, num_samples=100, save_dir='./visualizations')

    # 2. Create detailed individual samples (10 from each split)
    for split in ['train', 'val', 'test']:
        visualize_individual_samples(data_dir, split=split, num_samples=10,
                                     save_dir=f'./visualizations/{split}_samples')

    # 3. Create statistics summary
    visualize_with_statistics(data_dir, save_dir='./visualizations')

    print("\n" + "=" * 60)
    print("✓ ALL VISUALIZATIONS COMPLETE!")
    print("=" * 60)
    print("\nCreated:")
    print("  1. Grid visualizations: visualizations/train_rgb.png, val_rgb.png, test_rgb.png")
    print("  2. Mask visualizations: visualizations/train_masks.png, val_masks.png, test_masks.png")
    print("  3. Individual samples: visualizations/train_samples/, val_samples/, test_samples/")
    print("  4. Statistics summary: visualizations/dataset_statistics.png")