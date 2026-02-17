
import os
import sys

file_path = r"E:\work\AI_Antigravity\Test\aimodel\models--pyannote--segmentation-3.0\snapshots\e66f3d3b9eb0873085418a7b813d3b369bf160bb\pytorch_model.bin"

print(f"Checking HF_TOKEN...")
token = os.environ.get("HF_TOKEN")
if token:
    print(f"HF_TOKEN found: {token[:4]}...{token[-4:]}")
else:
    print("HF_TOKEN not found in environment variables.")

if os.path.exists(file_path):
    size = os.path.getsize(file_path)
    print(f"File exists. Size: {size} bytes")
else:
    print("File not found.")
