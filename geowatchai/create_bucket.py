#!/usr/bin/env python3
"""
Create Google Cloud Storage bucket for GEE exports
"""
import os
from google.cloud import storage
from google.auth import default

def create_bucket():
    try:
        # Get default credentials
        credentials, project_id = default()
        print(f"Using project: {project_id}")
        
        # Initialize storage client
        client = storage.Client(project=project_id, credentials=credentials)
        
        # Create bucket
        bucket_name = 'geo-vigil-guard-exports'
        bucket = client.create_bucket(bucket_name, location='US')
        print(f"Bucket {bucket_name} created successfully!")
        
        # Set bucket permissions for public read (optional)
        bucket.iam_configuration.uniform_bucket_level_access_enabled = True
        bucket.patch()
        
        print(f"Bucket configuration updated!")
        
    except Exception as e:
        print(f"Error creating bucket: {e}")
        print("Make sure you have authenticated with Google Cloud:")
        print("1. Run: gcloud auth application-default login")
        print("2. Or set GOOGLE_APPLICATION_CREDENTIALS environment variable")

if __name__ == "__main__":
    create_bucket()
