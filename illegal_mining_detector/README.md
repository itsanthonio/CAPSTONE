# Illegal Mining Detection with Sentinel-1

This project provides tools to download and process Sentinel-1 SAR data for detecting illegal mining activities.

## Setup

1. **Install Python dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Set up environment variables**:
   - Copy `.env.example` to `.env`
   - Add your Sentinel Hub credentials to `.env`

## Usage

### Downloading Sentinel-1 Data

To download the latest Sentinel-1 images:

```bash
python src/sentinel_downloader.py
```

This will:
1. Search for available Sentinel-1 images for the Tarkwa Gold Mine area in Ghana
2. Download the most recent VV and VH polarization images
3. Save them to `data/raw/`

### Customizing the Area of Interest

Edit the `bbox` parameter in the `main()` function of `sentinel_downloader.py` to change the area of interest. The format is `[min_lon, min_lat, max_lon, max_lat]`.

### Changing Date Range

Modify the `start_date` and `end_date` variables in the `main()` function to adjust the time period for image search.

## Project Structure

```
illegal_mining_detector/
├── data/
│   ├── raw/           # Downloaded Sentinel-1 images
│   └── tiles/         # Processed image tiles
├── labels/            # Annotation files (GeoJSON, masks, etc.)
├── models/            # Trained models
├── notebooks/         # Jupyter notebooks for analysis
├── src/               # Source code
│   └── sentinel_downloader.py  # Sentinel-1 data downloader
├── .env              # Environment variables (not in version control)
├── .gitignore        # Git ignore file
└── requirements.txt  # Python dependencies
```

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
