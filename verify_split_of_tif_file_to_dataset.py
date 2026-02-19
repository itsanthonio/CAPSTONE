# ===================================================================
# DIAGNOSTIC: Check what's in your data directories
# ===================================================================
from pathlib import Path

data_dir = Path('C:\\Users\\mcnob\\Documents\\Ashesi A\\Capstone\\optical_training_data')

for split in ['train', 'val', 'test']:
    image_dir = data_dir / split / 'images'
    mask_dir = data_dir / split / 'masks'

    image_files = sorted(list(image_dir.glob('*.npy')))
    mask_files = sorted(list(mask_dir.glob('*.npy')))

    print(f"\n{split.upper()}:")
    print(f"  Images: {len(image_files)}")
    print(f"  Masks:  {len(mask_files)}")

    if len(image_files) > 0:
        print(f"  First image: {image_files[0].name}")
        print(f"  Last image:  {image_files[-1].name}")

    if len(mask_files) > 0:
        print(f"  First mask: {mask_files[0].name}")
        print(f"  Last mask:  {mask_files[-1].name}")

    # Check for mismatches
    image_ids = set(int(f.stem.split('_')[1]) for f in image_files)
    mask_ids = set(int(f.stem.split('_')[1]) for f in mask_files)

    only_images = image_ids - mask_ids
    only_masks = mask_ids - image_ids

    if only_images:
        print(f"  ⚠️ Images without masks: {len(only_images)} files")
    if only_masks:
        print(f"  ⚠️ Masks without images: {len(only_masks)} files")