# -*- coding: utf-8 -*-
"""autoencoders_validation.ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1I7_7sk9EimUMtDNmfw1zAoYGV_nGIjia
"""

import pandas as pd
import numpy as np
import time
from sklearn.preprocessing import MinMaxScaler
from sqlalchemy import create_engine
from scipy.stats import zscore

# 🧠 Skip torch if you can't load it
try:
    import torch
    import torch.nn as nn
    torch_installed = True
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
except:
    print("⚠️ PyTorch not available. Skipping model-based anomaly detection.")
    torch_installed = False
    device = "cpu"

# ---------------------
# CONFIG
# ---------------------
SOURCE_DB_URL = "postgresql://neondb_owner:npg_6aN3WwcmuBvJ@ep-ancient-lab-abvko909-pooler.eu-west-2.aws.neon.tech/source"
TARGET_DB_URL = "postgresql://neondb_owner:npg_6aN3WwcmuBvJ@ep-ancient-lab-abvko909-pooler.eu-west-2.aws.neon.tech/target"
PRIMARY_KEY = "sale_id"

# ---------------------
# LOAD DATA
# ---------------------
def load_data(table_name, db_url):
    engine = create_engine(db_url)
    return pd.read_sql(f"SELECT * FROM {table_name}", engine)

t0 = time.time()
df_source = load_data("fact_sales_source", SOURCE_DB_URL)
df_target = load_data("fact_sales", TARGET_DB_URL)

# ---------------------
# COMMON COLUMNS ALIGNMENT
# ---------------------
common_cols = list(set(df_source.columns) & set(df_target.columns))
df_source = df_source[common_cols].copy()
df_target = df_target[common_cols].copy()

# ---------------------
# DUPLICATES
# ---------------------
source_dupes = df_source[df_source.duplicated(subset=PRIMARY_KEY, keep=False)]
target_dupes = df_target[df_target.duplicated(subset=PRIMARY_KEY, keep=False)]

# ---------------------
# DROP DUPLICATES FOR ALIGNMENT ONLY
# ---------------------
df_source_unique = df_source.drop_duplicates(subset=PRIMARY_KEY).set_index(PRIMARY_KEY).sort_index()
df_target_unique = df_target.drop_duplicates(subset=PRIMARY_KEY).set_index(PRIMARY_KEY).sort_index()
df_target_aligned = df_target_unique.reindex(df_source_unique.index)

# ---------------------
# DATA MISMATCH
# ---------------------
mismatches = []
for col in common_cols:
    if col == PRIMARY_KEY:
        continue
    mask = df_source_unique[col] != df_target_aligned[col]
    mismatch_df = pd.DataFrame({
        PRIMARY_KEY: df_source_unique.index[mask],
        f"{col}_source": df_source_unique[col][mask],
        f"{col}_target": df_target_aligned[col][mask]
    })
    if not mismatch_df.empty:
        mismatches.append(mismatch_df)

data_mismatch_df = pd.concat(mismatches, axis=0) if mismatches else pd.DataFrame()

# ---------------------
# NULLS
# ---------------------
source_nulls = df_source[df_source.isnull().any(axis=1)]
target_nulls = df_target[df_target.isnull().any(axis=1)]
nulls_combined = pd.merge(
    source_nulls, target_nulls,
    on=PRIMARY_KEY, suffixes=('_source', '_target'), how='outer'
)

# ---------------------
# OUTLIERS (Z-SCORE)
# ---------------------
numeric_cols = df_source.select_dtypes(include=['int64', 'float64']).columns
source_z = df_source[numeric_cols].apply(zscore)
target_z = df_target[numeric_cols].apply(zscore)
source_outliers = df_source[(np.abs(source_z) > 3).any(axis=1)]
target_outliers = df_target[(np.abs(target_z) > 3).any(axis=1)]
outliers_merged = pd.merge(
    source_outliers, target_outliers,
    on=PRIMARY_KEY, suffixes=('_source', '_target'), how='outer'
)

# ---------------------
# MISSING RECORDS IN TARGET
# ---------------------
missing_in_target = df_source_unique[~df_source_unique.index.isin(df_target_unique.index)].reset_index()

# ---------------------
# AUTOENCODER MODEL (optional)
# ---------------------
if torch_installed:
    model_start = time.time()
    df_numeric = df_source[numeric_cols].dropna()
    scaler = MinMaxScaler()
    X_scaled = scaler.fit_transform(df_numeric)
    X_tensor = torch.tensor(X_scaled, dtype=torch.float32).to(device)

    class Autoencoder(nn.Module):
        def __init__(self, input_size):
            super(Autoencoder, self).__init__()
            self.encoder = nn.Sequential(
                nn.Linear(input_size, 32),
                nn.ReLU(),
                nn.Linear(32, 16),
                nn.ReLU()
            )
            self.decoder = nn.Sequential(
                nn.Linear(16, 32),
                nn.ReLU(),
                nn.Linear(32, input_size)
            )

        def forward(self, x):
            return self.decoder(self.encoder(x))

    model = Autoencoder(X_tensor.shape[1]).to(device)
    criterion = nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)

    model.train()
    for epoch in range(100):
        output = model(X_tensor)
        loss = criterion(output, X_tensor)
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

    model.eval()
    with torch.no_grad():
        output = model(X_tensor)
        loss_vector = torch.mean((X_tensor - output) ** 2, dim=1)
        threshold = torch.mean(loss_vector) + 3 * torch.std(loss_vector)
        anomalies = loss_vector > threshold
        anomaly_indices = df_numeric.index[anomalies.cpu().numpy()]
        anomaly_df = df_source.loc[anomaly_indices]

    model_end = time.time()
else:
    anomaly_df = pd.DataFrame()
    model_start = model_end = 0

# ---------------------
# SAVE TO EXCEL
# ---------------------
with pd.ExcelWriter("sales_validation_report.xlsx", engine="xlsxwriter") as writer:
    if not data_mismatch_df.empty:
        data_mismatch_df.to_excel(writer, sheet_name="Data_Mismatches", index=False)
    nulls_combined.to_excel(writer, sheet_name="Nulls_Source_Target", index=False)
    source_dupes.to_excel(writer, sheet_name="Source_Duplicates", index=False)
    target_dupes.to_excel(writer, sheet_name="Target_Duplicates", index=False)
    outliers_merged.to_excel(writer, sheet_name="Outliers_SideBySide", index=False)
    missing_in_target.to_excel(writer, sheet_name="Missing_In_Target", index=False)
    anomaly_df.to_excel(writer, sheet_name="Model_Anomalies", index=False)

# ---------------------
# SUMMARY
# ---------------------
t1 = time.time()
print("\n✅ Validation complete. Report saved as 'sales_validation_report.xlsx'\n")

print("📊 Validation Summary:")
print(f"⏱️ Total validation time: {round(t1 - t0, 2)} seconds")
print(f"🔁 Data mismatches: {len(data_mismatch_df)}")
print(f"❓ Nulls in source or target: {len(nulls_combined)}")
print(f"📎 Duplicates in source: {len(source_dupes)}")
print(f"📎 Duplicates in target: {len(target_dupes)}")
print(f"📈 Outliers in source: {len(source_outliers)}")
print(f"📈 Outliers in target: {len(target_outliers)}")
print(f"🚫 Missing records in target: {len(missing_in_target)}")

if torch_installed:
    print("\n🧠 Autoencoder Model:")
    print(f"🕒 Training + inference time: {round(model_end - model_start, 2)} sec")
    print(f"⚠️ Anomalies detected: {len(anomaly_df)}")
    print(f"📉 Anomaly ratio: {round(len(anomaly_df)/len(df_numeric)*100, 2)}%")
    print(f"💻 Device: {'GPU' if device == 'cuda' else 'CPU'}")

# ---------------------
# PIPELINE STATUS
# ---------------------
sheets_to_check = [
    data_mismatch_df,
    nulls_combined,
    source_dupes,
    target_dupes,
    outliers_merged,
    missing_in_target,
    anomaly_df
]

pipeline_status = "FAIL" if any(not df.empty for df in sheets_to_check) else "PASS"
pd.DataFrame([{"status": pipeline_status}]).to_csv("pipeline_status.csv", index=False)
print(f"\n🚦 Pipeline Status: {pipeline_status}")

