"""Research-only ticket-specific probability engine.

Uses calibrated Win/Top2/Top3 marginals already produced by KeibaAI and fits a
race-local Plackett-Luce ranking distribution to all three targets. This avoids
naively multiplying marginal probabilities and gives coherent joint events for
Wide/Quinella/Exacta/Trio/Trifecta. No odds, stakes, or recommendations here.

The hot-path marginal calculation is vectorized, but the objective, finite-
difference optimizer, iteration count, learning-rate schedule, and probability
formulas are unchanged from v1. This is a computational optimization only.
"""
from __future__ import annotations
from functools import lru_cache
from itertools import permutations
import math
import numpy as np
VERSION = "ticket-probability-runtime-v1.1-20261031-vectorized"

@lru_cache(maxsize=32)
def _permutation_indices(n):
    """Cache ordered pair/triple index arrays for a field size."""
    p2=np.asarray(list(permutations(range(n),2)),dtype=np.intp)
    p3=np.asarray(list(permutations(range(n),3)),dtype=np.intp)
    return p2,p3

def _pl_marginals(weights):
    w=np.asarray(weights,dtype=float)
    if w.ndim!=1 or len(w)<3 or not np.isfinite(w).all() or (w<=0).any(): raise ValueError("positive finite race weights required")
    w=w/w.sum(); n=len(w); r2=np.zeros(n); r3=np.zeros(n)
    p2,p3=_permutation_indices(n)
    a,b=p2[:,0],p2[:,1]
    v2=w[a]*w[b]/(1-w[a])
    np.add.at(r2,b,v2)
    a,b,c=p3[:,0],p3[:,1],p3[:,2]
    v3=w[a]*w[b]/(1-w[a])*w[c]/(1-w[a]-w[b])
    np.add.at(r3,c,v3)
    return w,w+r2,w+r2+r3

def fit_race_weights(win,top2,top3,*,iterations=300,lr=.08):
    targets=[np.asarray(x,dtype=float) for x in (win,top2,top3)]; n=len(targets[0])
    if any(x.shape!=(n,) or not np.isfinite(x).all() for x in targets) or n<3: raise ValueError("aligned finite marginals required")
    if (targets[0]<=0).any() or abs(targets[0].sum()-1)>1e-5: raise ValueError("win probabilities must sum to one")
    if np.any(targets[1]<targets[0]-1e-8) or np.any(targets[2]<targets[1]-1e-8): raise ValueError("marginals must be monotone")
    z=np.log(np.clip(targets[0],1e-9,None)); z-=z.mean(); eps=1e-5
    def loss(v):
        a,b,c=_pl_marginals(np.exp(v-v.max()))
        return 2*np.mean((a-targets[0])**2)+np.mean((b-targets[1])**2)+np.mean((c-targets[2])**2)
    for _ in range(iterations):
        base=loss(z); grad=np.zeros(n)
        for i in range(n):
            zz=z.copy(); zz[i]+=eps; grad[i]=(loss(zz)-base)/eps
        candidate=z-lr*grad; candidate-=candidate.mean()
        if loss(candidate)<=base: z=candidate
        else: lr*=.5
        if lr<1e-6: break
    w=np.exp(z-z.max()); return w/w.sum()

def distributions(numbers,win,top2,top3):
    numbers=list(numbers); n=len(numbers)
    if len(set(numbers))!=n: raise ValueError("unique horse numbers required")
    w=fit_race_weights(win,top2,top3); exacta={}; quinella={}; trifecta={}; trio={}
    for i,j in permutations(range(n),2):
        p=w[i]*w[j]/(1-w[i]); exacta[(numbers[i],numbers[j])]=p; u=tuple(sorted((numbers[i],numbers[j]))); quinella[u]=quinella.get(u,0)+p
    for i,j,k in permutations(range(n),3):
        p=w[i]*w[j]/(1-w[i])*w[k]/(1-w[i]-w[j]); key=(numbers[i],numbers[j],numbers[k]); trifecta[key]=p; u=tuple(sorted(key)); trio[u]=trio.get(u,0)+p
    wide={pair:sum(p for key,p in trio.items() if pair[0] in key and pair[1] in key) for pair in quinella}
    for name,d in (("exacta",exacta),("quinella",quinella),("trifecta",trifecta),("trio",trio)):
        if not math.isclose(sum(d.values()),1,abs_tol=1e-8): raise RuntimeError(f"{name} probability mass mismatch")
    return {"weights":dict(zip(numbers,w)),"wide":wide,"quinella":quinella,"exacta":exacta,"trio":trio,"trifecta":trifecta}
