import pandas as pd

df = pd.read_csv("dataset/styles.csv", engine="python", on_bad_lines="skip")

material_map = {
    "Shirts": "Cotton",
    "Tshirts": "Cotton",
    "Tops": "Cotton",
    "Kurtas": "Cotton",

    "Jeans": "Denim",
    "Trousers": "Denim",
    "Track Pants": "Polyester",
    "Shorts": "Polyester",

    "Sweaters": "Wool",
    "Sweatshirts": "Polyester",
    "Jackets": "Polyester",
    "Coats": "Polyester",
    "Blazers": "Polyester",

    "Dresses": "Chiffon",
    "Skirts": "Chiffon",

    "Shoes": "Leather",
    "Sandals": "Leather",
    "Flip Flops": "Rubber",
    "Sports Shoes": "Synthetic",

    "Bags": "Canvas",
    "Backpacks": "Canvas",
    "Handbags": "Leather",

    "Watches": "Metal",
    "Belts": "Leather",
    "Sunglasses": "Plastic",
}

df["material"] = df["articleType"].map(material_map).fillna("Mixed")

df["internal_tag"] = df["id"].map(lambda x: f"CLTH-{int(x):06d}")

df.to_csv("dataset/products_catalog.csv", index=False)

print("catalog prepared:", df.shape)
