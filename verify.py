import numpy as np
import matplotlib.pyplot as plt
import os
import random

# Paths to your newly created local folders
img_dir = './training_data/images'
mask_dir = './training_data/masks'

def final_verification(num_samples=4):
    all_patches = sorted([f for f in os.listdir(img_dir) if f.endswith('.npy')])
    
    # Pick random samples to ensure the whole dataset is good
    sample_files = random.sample(all_patches, num_samples)
    
    fig, axes = plt.subplots(num_samples, 3, figsize=(15, 5 * num_samples))
    
    for i, file_name in enumerate(sample_files):
        img = np.load(os.path.join(img_dir, file_name))
        mask = np.load(os.path.join(mask_dir, file_name.replace('patch', 'mask')))
        
        # Channel 0: 2021 VH | Channel 2: 2024 VH
        vh_2021 = img[0]
        vh_2024 = img[2]
        
        # Plotting
        axes[i, 0].imshow(vh_2021, cmap='gray')
        axes[i, 0].set_title(f"Patch {file_name}: 2021 VH")
        
        axes[i, 1].imshow(vh_2024, cmap='gray')
        axes[i, 1].set_title("2024 VH (Current)")
        
        # Overlay Mask on 2024 image to check alignment
        axes[i, 2].imshow(vh_2024, cmap='gray')
        axes[i, 2].imshow(mask, cmap='jet', alpha=0.5) # Heatmap overlay
        axes[i, 2].set_title("Mask Overlaid on 2024")
        
        # Stats per patch
        print(f"Patch {file_name} -> Max: {img.max():.2f}, Mean: {img.mean():.2f}, Mask Pixels: {np.sum(mask)}")

    plt.tight_layout()
    plt.show()

final_verification()