
import os
import shutil
from huggingface_hub import snapshot_download

# Configuration
HF_TOKEN = os.getenv("HF_TOKEN", "")
CACHE_DIR = r"E:\work\AI_Antigravity\Test\aimodel"
REPO_ID = "pyannote/segmentation-3.0"
CORRUPTED_FILE_PATH = r"E:\work\AI_Antigravity\Test\aimodel\models--pyannote--segmentation-3.0\snapshots\e66f3d3b9eb0873085418a7b813d3b369bf160bb\pytorch_model.bin"

print(f"Setting HF_TOKEN...")
os.environ["HF_TOKEN"] = HF_TOKEN

# 1. Remove corrupted file
if os.path.exists(CORRUPTED_FILE_PATH):
    print(f"Removing corrupted file: {CORRUPTED_FILE_PATH}")
    try:
        os.remove(CORRUPTED_FILE_PATH)
        print("File removed successfully.")
    except Exception as e:
        print(f"Error removing file: {e}")
else:
    print("Corrupted file not found (maybe already removed or path incorrect).")
    # Verify directory exists
    dir_path = os.path.dirname(CORRUPTED_FILE_PATH)
    if os.path.exists(dir_path):
        print(f"Directory exists: {dir_path}")
    else:
        print(f"Directory NOT found: {dir_path}")


# 2. Re-download
print(f"Re-downloading {REPO_ID} to {CACHE_DIR}...")
try:
    # force_download=False will check for existing files. 
    # Since we deleted the specific file, it should re-download it.
    # If that fails to trigger, we might need force_download=True
    path = snapshot_download(
        repo_id=REPO_ID,
        token=HF_TOKEN,
        cache_dir=CACHE_DIR,
        force_download=False 
    )
    print(f"Download complete! Path: {path}")
    
    # Check if file exists now
    if os.path.exists(CORRUPTED_FILE_PATH):
         size = os.path.getsize(CORRUPTED_FILE_PATH)
         print(f"New file size: {size} bytes")
    else:
         print("WARNING: File still does not exist at expected path (Snapshot ID might have changed?)")

except Exception as e:
    print(f"Download failed: {e}")

print("Fix process finished.")
