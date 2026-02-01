import numpy as np
import matplotlib.pyplot as plt
import os
import random

def visualize_optical_patches(data_dir, num_samples=3):
    image_path = os.path.join(data_dir, 'images')
    mask_path = os.path.join(data_dir, 'masks')
    
    # Get all patch filenames
    patch_files = [f for f in os.listdir(image_path) if f.endswith('.npy')]
    sample_files = random.sample(patch_files, num_samples)

    fig, axes = plt.subplots(num_samples, 3, figsize=(15, 5 * num_samples))

    for i, file_name in enumerate(sample_files):
        # Load the 22-band image and the mask
        img = np.load(os.path.join(image_path, file_name)) # (256, 256, 22)
        mask = np.load(os.path.join(mask_path, file_name.replace('patch_', 'mask_')))

        # 1. Prepare RGB (Bands 3, 2, 1 correspond to B4, B3, B2 in your merge)
        # Note: Indexing starts at 0, so Band 3 is index 2.
        rgb = img[:, :, [2, 1, 0]]
        
        # 2. Prepare BSI (Usually the 20th band / index 19)
        bsi = img[:, :, 19]

        # Helper function for stretching
        def stretch(data):
            # Ignore 0s (NoData) for the calculation
            valid = data[data > 0]
            if valid.size == 0: return data
            p2, p98 = np.percentile(valid, 2), np.percentile(valid, 98)
            return np.clip((data - p2) / (p98 - p2 + 1e-6), 0, 1)

        # Plotting
        axes[i, 0].imshow(stretch(rgb))
        axes[i, 0].set_title(f"{file_name}\nTrue Color (RGB)")
        axes[i, 0].axis('off')

        axes[i, 1].imshow(stretch(bsi), cmap='magma') # 'magma' makes mines look like hot spots
        axes[i, 1].set_title("Expert's Baresoil Index")
        axes[i, 1].axis('off')

        axes[i, 2].imshow(mask, cmap='gray')
        axes[i, 2].set_title("Ground Truth Mask")
        axes[i, 2].axis('off')

    plt.tight_layout()
    plt.show()

# Run it!
visualize_optical_patches('./optical_training_data')