"""Research-only ticket-specific probability engine.

Uses calibrated Win/Top2/Top3 marginals already produced by KeibaAI and fits a
race-local Plackett-Luce ranking distribution to all three targets.  This avoids
naively multiplying marginal probabilities and gives coherent joint events for
Wide/Quinella/Exacta/Trio/Trifecta.  No odds, stakes, or recommendations here.
"""
from __future__ import annotations

from itertools import permutations
import math
import numpy as np

VERSION = "ticket-probability-runtime-v1-20261031"


def _pl_marginals(weights: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    w = np.asarray(weights, dtype=float)
    if w.ndim != 1 or len(w) < 3 or not np.isfinite(w).all() or (w <= 0).any():
        raise ValueError("positive finite race weights required")
    w = w / w.sum()
    n = len(w)
    r1 = w.copy()
    r2 = np.zeros(n); r3 = np.zeros(n)
    for a,b,c in permutations(range(n),3):
        p2 = w[a] * w[b] / (1.0-w[a])
        # Count second only once per ordered (a,b), not once for every c.
        if c == next(k for k in range(n) if k not in (a,b)):
            pass
        p3 = p2 * w[c] / (1.0-w[a]-w[b])
        r3[c] += p3
    for a,b in permutations(range(n),2):
        r2[b] += w[a] * w[b] / (1.0-w[a])
    return r1, r1+r2, r1+r2+r3


def fit_race_weights(win, top2, top3, *, iterations: int = 500, lr: float = .08) -> np.ndarray:
    """Fit PL strengths to Win/Top2/Top3 marginals in log-space.

    Win is intentionally weighted most because it is a normalized race
    probability; Top2/Top3 add placement information from their dedicated
    calibrated models. This is a deterministic research bridge until a direct
    listwise ranking model passes OOS validation.
    """
    targets = [np.asarray(x,dtype=float) for x in (win,top2,top3)]
    n = len(targets[0])
    if any(x.shape != (n,) or not np.isfinite(x).all() for x in targets) or n < 3:
        raise ValueError("aligned finite marginals required")
    if (targets[0] <= 0).any() or abs(targets[0].sum()-1) > 1e-5:
        raise ValueError("win probabilities must sum to one")
    if np.any(targets[1] < targets[0]-1e-8) or np.any(targets[2] < targets[1]-1e-8):
        raise ValueError("marginals must be monotone")
    z = np.log(np.clip(targets[0],1e-9,None)); z -= z.mean()
    eps = 1e-5
    def loss(v):
        w=np.exp(v-v.max()); a,b,c=_pl_marginals(w)
        return (2.0*np.mean((a-targets[0])**2)+np.mean((b-targets[1])**2)+np.mean((c-targets[2])**2))
    # Small n (<=18): deterministic finite-difference optimization is adequate
    # for research runtime and avoids adding another training dependency.
    for _ in range(iterations):
        base=loss(z); grad=np.zeros(n)
        for i in range(n):
            zz=z.copy(); zz[i]+=eps; grad[i]=(loss(zz)-base)/eps
        candidate=z-lr*grad; candidate-=candidate.mean()
        if loss(candidate) <= base:
            z=candidate
        else:
            lr*=.5
        if lr < 1e-6:
            break
    w=np.exp(z-z.max()); return w/w.sum()


def distributions(numbers, win, top2, top3):
    numbers=list(numbers); n=len(numbers)
    if len(set(numbers)) != n:
        raise ValueError("unique horse numbers required")
    w=fit_race_weights(win,top2,top3)
    exacta={}; quinella={}; trifecta={}; trio={}
    for i,j in permutations(range(n),2):
        p=w[i]*w[j]/(1-w[i])
        exacta[(numbers[i],numbers[j])]=p
        k=tuple(sorted((numbers[i],numbers[j]))); quinella[k]=quinella.get(k,0)+p
    for i,j,k in permutations(range(n),3):
        p=w[i]*w[j]/(1-w[i])*w[k]/(1-w[i]-w[j])
        key=(numbers[i],numbers[j],numbers[k]); trifecta[key]=p
        u=tuple(sorted(key)); trio[u]=trio.get(u,0)+p
    # Wide = probability both horses occur anywhere in top3.
    wide={}
    for pair in quinella:
        wide[pair]=sum(p for key,p in trio.items() if pair[0] in key and pair[1] in key)
    for name,d,total in (("exacta",exacta,1),("quinella",quinella,1),("trifecta",trifecta,1),("trio",trio,1)):
        if not math.isclose(sum(d.values()),total,abs_tol=1e-8):
            raise RuntimeError(f"{name} probability mass mismatch")
    return {"weights":dict(zip(numbers,w)),"wide":wide,"quinella":quinella,"exacta":exacta,"trio":trio,"trifecta":trifecta}
