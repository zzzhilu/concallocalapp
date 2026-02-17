
import torch
import os
import sys

path = r"E:\work\AI_Antigravity\Test\aimodel\models--pyannote--segmentation-3.0\snapshots\e66f3d3b9eb0873085418a7b813d3b369bf160bb\pytorch_model.bin"

print(f"Verifying file: {path}")

if not os.path.exists(path):
    print("File not found!")
    sys.exit(1)

print(f"Size: {os.path.getsize(path)} bytes")

try:
    print("Attempting to load...")
    # Try with weights_only=False to bypass safety check for this verification
    try:
        state_dict = torch.load(path, map_location='cpu', weights_only=False)
    except TypeError:
        # Fallback for older torch versions
        state_dict = torch.load(path, map_location='cpu')
        
    print("✅ Successfully loaded pytorch_model.bin")
    if isinstance(state_dict, dict):
        print(f"Keys sample: {list(state_dict.keys())[:3]}")
    else:
        print(f"Loaded object type: {type(state_dict)}")
        
except Exception as e:
    print(f"❌ Failed to load: {e}")
    # Print more details about the exception
    import traceback
    traceback.print_exc()

