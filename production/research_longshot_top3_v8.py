"""V8: contextual shrinkage of the frozen V2 market residual.
Research only; final odds, no live promotion, no ticket decisions.
2025 Jan-Aug fit / Sep-Dec select, 2026 retrospective comparison.
"""
from datetime import datetime
import hashlib
import json
import sys
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit
import lightgbm as lgb

from train_longshot_top3 import ROOT, HISTORY, JST, logit, prepare_longshot, split_longshot
from train_no_current_odds_challenger import ensure, prepare_history
from evaluate_no_current_odds_2026_history import prepare_2026
from research_longshot_top3_v2 import (
    FEATURE_SETS, add_extras, place_history, paired, train as train_v2,
    predict as predict_v2, OUTPUT as V2_OUT, VERSION as V2_VERSION,
)
from research_longshot_top3_v7 import evaluate as rank_evaluate

OUT = ROOT / "research/model_candidates/longshot_top3_v8"
WEIGHTS = (0., .25, .5, .75, 1., 1.25, 1.5)
PENALTIES = (.01, .1, 1.)
NAMES = ("logWinOdds", "popularityFraction", "fieldSize",
         "longshotCount", "longshotMarketMass")
MAX_WEIGHT = 1.5


def inputs(d, p):
    """Uses only race identity, pre-race odds, field size and frozen scores."""
    d = d.reset_index(drop=True)
    m = d["市場3着内基準"].to_numpy(float)
    p = np.asarray(p, float)
    ensure(len(p) == len(d) and len(d) > 0, "bad V8 cohort")
    ensure(np.isfinite(p).all() and np.isfinite(m).all()
           and ((m > 0) & (m < 1) & (p > 0) & (p < 1)).all(),
           "invalid probabilities")
    ensure(not d.duplicated(["レースID", "馬番"]).any(), "duplicate horse")
    g = d.groupby("レースID", sort=False)
    odds = d["単勝オッズ"].to_numpy(float)
    field = d["頭数"].to_numpy(float)
    count = g["馬番"].transform("size").to_numpy(float)
    mass = g["市場3着内基準"].transform("sum").to_numpy(float)
    ensure((odds >= 10).all() and (field >= count).all()
           and (field > 0).all(), "invalid longshot field")
    x = np.column_stack([np.log(odds), d["人気"].to_numpy(float)/field,
                         field, count, mass])
    ensure(np.isfinite(x).all(), "nonfinite context")
    return x, m, logit(p) - logit(m), 1./count


def fixed(d, p, weight):
    ensure(weight in WEIGHTS, "unregistered fixed weight")
    _, m, residual, _ = inputs(d, p)
    if weight == 0.: return m.copy()
    if weight == 1.: return np.asarray(p, float).copy()
    return expit(logit(m) + weight * residual)


def fit(d, p, penalty):
    ensure(penalty in PENALTIES, "unregistered penalty")
    ensure(pd.to_datetime(d["日付"]).dt.year.eq(2025).all(), "fit outside 2025")
    x, m, residual, w = inputs(d, p)
    y = d["着順"].le(3).to_numpy(float)
    ensure(y.sum() > 0 and y.sum() < len(y), "degenerate target")
    w /= w.sum()
    mu = np.average(x, axis=0, weights=w)
    scale = np.maximum(np.sqrt(np.average((x-mu)**2, axis=0, weights=w)), 1e-8)
    X = np.column_stack([np.ones(len(x)), (x-mu)/scale])
    z0 = logit(m)
    def objective(beta):
        s = expit(X @ beta)
        z = z0 + MAX_WEIGHT*s*residual
        loss = np.sum(w*(np.logaddexp(0,z)-y*z)) + .5*penalty*np.sum(beta[1:]**2)
        grad = X.T @ (w*(expit(z)-y)*residual*MAX_WEIGHT*s*(1-s))
        grad[1:] += penalty*beta[1:]
        return loss, grad
    init = np.zeros(X.shape[1]); init[0] = np.log(2.)
    opt = minimize(objective, init, jac=True, method="L-BFGS-B",
                   options={"maxiter": 300, "ftol": 1e-11})
    ensure(opt.success and np.isfinite(opt.x).all(), "V8 optimizer failure")
    return {"features": list(NAMES), "penalty": penalty, "mean": mu.tolist(),
            "scale": scale.tolist(), "coefficients": opt.x.tolist()}


def predict(d, p, model):
    ensure(model["features"] == list(NAMES), "context feature identity")
    x, m, residual, _ = inputs(d, p)
    X = np.column_stack([np.ones(len(x)),
        (x-np.asarray(model["mean"]))/np.asarray(model["scale"])])
    a = MAX_WEIGHT*expit(X @ np.asarray(model["coefficients"]))
    q = expit(logit(m) + a*residual)
    ensure(np.isfinite(q).all() and np.isfinite(a).all(), "invalid V8 output")
    return q, a


def brier(d, p):
    _, _, _, w = inputs(d, p)
    return float(np.average((p-d["着順"].le(3).to_numpy(float))**2, weights=w))


def select(d, p):
    dev = d["日付"].lt(pd.Timestamp("2025-09-01")).to_numpy()
    a, b = d.loc[dev].reset_index(drop=True), d.loc[~dev].reset_index(drop=True)
    ensure(len(a) and len(b), "missing development or selection")
    ensure(pd.to_datetime(d["日付"]).dt.year.eq(2025).all(), "selection outside 2025")
    rows = [{"spec": "v2", "weight": 1., "brier": brier(b, p[~dev])}]
    for weight in WEIGHTS:
        if weight != 1.:
            rows.append({"spec": "fixed", "weight": weight,
                         "brier": brier(b, fixed(b,p[~dev],weight))})
    for penalty in PENALTIES:
        m = fit(a,p[dev],penalty)
        rows.append({"spec": "context", "penalty": penalty,
                     "brier": brier(b,predict(b,p[~dev],m)[0])})
    key = lambda row: (row["brier"], {"v2":0,"fixed":1,"context":2}[row["spec"]],
                       abs(row.get("weight",1.)-1.), -row.get("penalty",0.))
    best = min(rows,key=key)
    best_fixed = min((row for row in rows if row["spec"] != "context"),key=key)
    best_context = min((row for row in rows if row["spec"] == "context"),key=key)
    return {"selected": best,"bestFixed":best_fixed,"bestContext":best_context,
            "grid2025":rows,"developmentRaces":int(a["レースID"].nunique()),
            "selectionRaces":int(b["レースID"].nunique()),
            "devEnd":"2025-08-31","selectionStart":"2025-09-01"}


def main():
    hist = pd.read_parquet(HISTORY)
    prior = place_history(hist)
    safe = hist.copy()
    safe["馬体重"] = pd.to_numeric(safe["馬体重"],errors="coerce").fillna(480.)
    train, valid, hold = split_longshot(add_extras(
        prepare_longshot(prepare_history(safe)),prior))
    report = json.loads((V2_OUT/"research.json").read_text())
    choice = report["selection2024"]["selected"]
    features = FEATURE_SETS["place_and_market"]
    ensure(report["version"] == V2_VERSION and report["researchOnly"] is True
           and report["candidatePromotionApproved"] is False
           and report["selectedFeatures"] == list(features)
           and choice["featureSet"] == "place_and_market", "V2 contract")
    provisional, rounds = train_v2(train,features,valid)
    ensure(rounds == choice["rounds"], "V2 historical replay changed")
    d = hold.reset_index(drop=True)
    base = predict_v2(d,provisional,features,choice["weight"])
    protocol = select(d,base)
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/"selection2025.json").write_text(json.dumps(protocol,indent=2,allow_nan=False)+"\n")
    print("V8_SELECTION_LOCKED",json.dumps(protocol["selected"]),flush=True)
    model = fit(d,base,protocol["bestContext"]["penalty"])
    (OUT/"model_through2025.json").write_text(json.dumps(model,indent=2,allow_nan=False)+"\n")
    recent = add_extras(prepare_longshot(prepare_2026(safe)),prior).reset_index(drop=True)
    frozen = V2_OUT/"model_through2025.txt"
    sha = hashlib.sha256(frozen.read_bytes()).hexdigest()
    ensure(sha == report["modelHashes"][frozen.name], "V2 frozen digest")
    p = predict_v2(recent,lgb.Booster(model_file=str(frozen)),features,choice["weight"])
    ctx, alpha = predict(recent,p,model)
    methods = {"market":recent["市場3着内基準"].to_numpy(float),
               "v2":p,"fixed":fixed(recent,p,protocol["bestFixed"]["weight"]),
               "context":ctx}
    methods["selected"] = methods[protocol["selected"]["spec"]]
    metrics = {name: {"equalRaceBrier": brier(recent,q),
                      "pairedVsV2":paired(recent,q,p),
                      "pairedVsMarket":paired(recent,q,methods["market"])}
               for name,q in methods.items()}
    result = {"version":"longshot-v8-contextual-residual-20261127",
              "generatedAtJST":datetime.now(JST).isoformat(),
              "researchOnly":True,"candidatePromotionApproved":False,
              "formalPredictionAIChanged":False,"autoBetEnabled":False,
              "formalRecommendationsEnabled":False,
              "sourceHistorySha256":hashlib.sha256(HISTORY.read_bytes()).hexdigest(),
              "v2ModelSha256":sha,"protocol":protocol,"2026":{"probability":metrics,
              "shortlist":rank_evaluate(recent,methods),
              "contextWeightQuantiles":np.quantile(alpha,[0,.1,.5,.9,1]).tolist()},
              "limitations":["Final odds; already examined historical years, not an independent prospective test.",
              "Only existing V2 input context; no new live source.",
              "Context fitting uses out-of-training V2 predictions and equal-race weighted log loss.",
              "Selection uses 2025 equal-race Brier only; no 2026 tuning.",
              "No financial return estimate or deployment change."]}
    (OUT/"research.json").write_text(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False)+"\n")
    print("V8_RESEARCH_RESULT",json.dumps(result,ensure_ascii=False,allow_nan=False),flush=True)


def self_test():
    d = pd.DataFrame({"レースID":["a","a","b","b","b"],"馬番":[1,2,1,2,3],
        "市場3着内基準":[.2,.1,.15,.12,.04],"単勝オッズ":[12,25,15,30,90],
        "頭数":[10,10,12,12,12],"人気":[6,8,6,8,12],
        "着順":[1,9,8,2,12],"日付":pd.to_datetime(["2025-01-01"]*5)})
    p = np.array([.24,.08,.2,.15,.03])
    np.testing.assert_array_equal(fixed(d,p,1.),p)
    np.testing.assert_array_equal(fixed(d,p,0.),d["市場3着内基準"])
    model = fit(d,p,.1)
    q, a = predict(d,p,model)
    assert ((a>=0)&(a<=MAX_WEIGHT)).all()
    np.testing.assert_allclose(predict(d.assign(着順=99),p,model)[0],q)
    ix = [3,0,4,1,2]
    np.testing.assert_allclose(predict(d.iloc[ix],p[ix],model)[0],q[ix])
    np.testing.assert_allclose(predict(d,d["市場3着内基準"].to_numpy(),model)[0],
                               d["市場3着内基準"])
    assert np.isclose(brier(d,p), np.mean([np.mean((p[:2]-[1,0])**2),
                                           np.mean((p[2:]-[0,1,0])**2)]))
    print("V8_SELF_TEST_OK")


if __name__ == "__main__":
    if "--self-test" in sys.argv: self_test()
    else: main()
