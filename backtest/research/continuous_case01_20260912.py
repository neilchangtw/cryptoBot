"""P: fixed 15-bar extreme age, no outcome labels or current-bar prices."""
from pathlib import Path
import numpy as np
import pandas as pd

NUMBER=1
ID='P_extreme_age'
NAME='突破邊界年齡'
PREREG='doc/continuous_round01_extreme_age_20260912.md'
TEST='tests/test_continuous_case01_20260912.py'
NEIGHBORS=[6,8]

def features(d,parameter=None):
    limit=7 if parameter is None else parameter
    c=d.close.to_numpy(dtype=float);n=len(c)
    age_l=np.full(n,np.nan);age_s=np.full(n,np.nan)
    for i in range(15,n):
        prior=c[i-15:i]
        if not np.isfinite(prior).all():continue
        age_l[i]=int(np.argmax(prior[::-1]))+1
        age_s[i]=int(np.argmin(prior[::-1]))+1
    valid=np.isfinite(age_l)&np.isfinite(age_s)
    return pd.DataFrame({'decision_ts':d.datetime+pd.Timedelta(hours=1),'source_last_close':d.datetime,
        'age_L':age_l,'age_S':age_s,'valid':valid,
        'allow_L':valid&(age_l<=limit),'allow_S':valid&(age_s<=limit)})
