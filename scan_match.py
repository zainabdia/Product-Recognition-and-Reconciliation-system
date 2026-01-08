import sys
import pandas as pd
from PIL import Image
import imagehash

INDEX = "dataset/index_phash.csv"

if len(sys.argv) < 2:
    print("usage: python scan_match.py <image_path>")
    sys.exit(1)

scan_path = sys.argv[1]

df = pd.read_csv(INDEX)

scan_img = Image.open(scan_path).convert("RGB")
scan_hash = imagehash.phash(scan_img)

# compute hamming distance
df["dist"] = df["phash"].apply(lambda h: scan_hash - imagehash.hex_to_hash(h))

top = df.nsmallest(5, "dist")[[
    "id","productDisplayName","articleType","material",
    "baseColour","gender","internal_tag","image_path","dist"
]]

print("\nSCAN IMAGE:", scan_path)
print("\nTOP MATCHES:")
print(top.to_string(index=False))

best = top.iloc[0]

print("\nBEST MATCH")
print("name:", best["productDisplayName"])
print("material:", best["material"])
print("internal tag:", best["internal_tag"])
print("distance:", best["dist"], "(0 = identical)")
