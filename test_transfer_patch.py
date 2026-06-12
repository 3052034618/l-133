import pandas as pd

df = pd.read_csv("examples/stock.csv")

df.loc[(df["store_id"] == "S003") & (df["sku"] == "SKU001"), "current_stock"] = 2000
df.loc[(df["store_id"] == "S003") & (df["sku"] == "SKU002"), "current_stock"] = 3000
df.loc[(df["store_id"] == "S005") & (df["sku"] == "SKU007"), "current_stock"] = 4000
df.loc[(df["store_id"] == "S005") & (df["sku"] == "SKU005"), "current_stock"] = 6000

df.to_csv("examples/stock.csv", index=False)
print("已修改部分门店库存为富余状态，用于测试调拨功能")
