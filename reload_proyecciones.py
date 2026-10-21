import sys
import pandas as pd

sys.path.insert(0, 'backend')
from app.data.sql_server import load_proyecciones_pyc

df = pd.read_parquet('etl/d2.parquet')
r = load_proyecciones_pyc(df)
print(r)
