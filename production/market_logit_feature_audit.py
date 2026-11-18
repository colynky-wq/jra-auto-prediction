"""Read-only LightGBM split audit: quantify use of market-logit feature.

Do not retrain, score horses, infer causal feature contributions or change
production model files. Split frequency/gain are model-level diagnostics only.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path


def audit_model_text(content):
    names = None
    tree_splits = []
    split_gains = []
    features_count = Counter()
    gains = defaultdict(float)
    trees = 0
    for line in content.splitlines():
        if line.startswith("feature_names="):
            if names is not None:
                raise ValueError("duplicate feature_names declaration")
            names = line.partition("=")[2].split()
        elif line.startswith("Tree="):
            trees += 1
        elif line.startswith("split_feature="):
            tree_splits = [int(x) for x in line.partition("=")[2].split()]
        elif line.startswith("split_gain="):
            split_gains = [float(x) for x in line.partition("=")[2].split()]
            if len(tree_splits) != len(split_gains):
                raise ValueError("mismatch split features and split gains")
            for index, gain in zip(tree_splits, split_gains):
                if index < 0 or not math.isfinite(gain) or gain < 0:
                    raise ValueError("invalid model split feature or gain")
                features_count[index] += 1
                gains[index] += gain
            tree_splits, split_gains = [], []
    if not names or trees < 1:
        raise ValueError("not a recognized LightGBM text model")
    if not features_count:
        raise ValueError("no valid model splits found")
    if any(i >= len(names) for i in features_count):
        raise ValueError("split feature index out of range")
    market_index = names.index("市場ロジット") if "市場ロジット" in names else None
    total_count = sum(features_count.values())
    total_gain = sum(gains.values())
    top = sorted(features_count,key=lambda i:gains[i],reverse=True)
    return {
        "featureCount":len(names), "treeCount":trees,
        "totalSplits":total_count, "totalSplitGain":round(total_gain,3),
        "marketLogitPresent":market_index is not None,
        "marketLogitFeatureIndex":market_index,
        "marketLogitSplitCount":features_count[market_index] if market_index is not None else 0,
        "marketLogitSplitGain":round(gains[market_index],3) if market_index is not None else 0.0,
        "marketLogitSplitSharePct":round(100*features_count[market_index]/total_count,3)
            if market_index is not None else 0.0,
        "marketLogitGainSharePct":round(100*gains[market_index]/total_gain,3)
            if market_index is not None and total_gain>0 else 0.0,
        "topFeaturesBySplitGain":[{
            "feature":names[i], "index":i,
            "splits":features_count[i],
            "splitGain":round(gains[i],3),
        } for i in top[:10]],
        "causalEffectEstimated":False,
        "featureAblationPerformed":False,
        "productionModelChanged":False,
    }


def audit_file(path):
    raw=Path(path).read_bytes()
    result=audit_model_text(raw.decode("utf-8"))
    result["modelPath"]=str(path)
    result["modelSha256"]=hashlib.sha256(raw).hexdigest()
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model",type=Path,
        default=Path("production/lightgbm_fixed_2025.txt"))
    args=parser.parse_args()
    print(json.dumps(audit_file(args.model),ensure_ascii=False,
                     indent=2,allow_nan=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
