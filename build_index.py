import pandas as pd
from PIL import Image
import imagehash

CATALOG = "dataset/products_catalog_with_images.csv"
OUT = "dataset/index_phash.csv"

df = pd.read_csv(CATALOG)

hashes = []
failed = 0

for p in df["image_path"]:
    try:
        img = Image.open(p).convert("RGB")
        h = imagehash.phash(img)
        hashes.append(str(h))
    except:
        hashes.append("")
        failed += 1

df["phash"] = hashes
df = df[df["phash"] != ""]

df.to_csv(OUT, index=False)

print("index built:", len(df))
print("failed images:", failed)
