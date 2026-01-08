import pandas as pd
from pathlib import Path

df = pd.read_csv("dataset/products_catalog.csv")

img_dir = Path("dataset/images")
df["image_path"] = df["id"].astype(str).apply(lambda x: str(img_dir / f"{x}.jpg"))

# mark whether the file exists
df["image_exists"] = df["image_path"].apply(lambda p: Path(p).exists())

print("total rows:", len(df))
print("images found:", df["image_exists"].sum())
print("missing images:", (~df["image_exists"]).sum())

# keep only rows that actually have an image (important for recognition)
df2 = df[df["image_exists"]].copy()
df2.to_csv("dataset/products_catalog_with_images.csv", index=False)

print("saved:", "dataset/products_catalog_with_images.csv", "rows:", len(df2))
