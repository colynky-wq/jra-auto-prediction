"""Research-only ticket-specific probability engine.

Uses calibrated Win/Top2/Top3 marginals already produced by KeibaAI and fits a
race-local Plackett-Luce ranking distribution to all three targets. This avoids
naively multiplying marginal probabilities and gives coherent joint events for
Wide/Quinella/Exacta/Trio/Trifecta. No odds, stakes, or recommendations here.

The optimizer keeps the original forward finite-difference objective, iteration
count, learning-rate schedule, and acceptance rule. Gradient perturbations are
evaluated as one NumPy batch. PL Top2/Top3 marginals use an algebraically
equivalent O(n^2) formulation instead of enumerating all ordered triples.
"""
from __future__ import annotations
from functools import lru_cache
from itertools import permutations
import math
import numpy as np
VERSION = "ticket-probability-runtime-v1.3-20261031-algebraic-pl"

@lru_cache(maxsize=32)
def _permutation_indices(n):
    p2=np.asarray(list(permutations(range(n),2)),dtype=np.intp)
    p3=np.asarray(list(permutations(range(n),3)),dtype=np.intp)
    return p2,p3

def _pl_marginals_batch(weights):
    """Return PL Win/Top2/Top3 marginals for one or more races.

    This is algebraically identical to summing every ordered pair/triple, but it
    avoids the O(n^3) ordered-triple materialization. For third-place mass, let
    Q[a,b] be the probability factor for ordered first=a, second=b before the
    final horse weight. The third-place mass for horse j is w[j] times the sum
    of Q over pairs containing neither j; this is total Q minus row/column j.
    """
    w=np.asarray(weights,dtype=float)
    if w.ndim==1: w=w[None,:]
    if w.ndim!=2 or w.shape[1]<3 or not np.isfinite(w).all() or (w<=0).any(): raise ValueError("positive finite race weights required")
    w=w/w.sum(axis=1,keepdims=True); batch,n=w.shape

    # Second-place contribution: sum_a!=j w[a] * w[j] / (1-w[a]).
    odds=w/(1-w)
    r2=w*(odds.sum(axis=1,keepdims=True)-odds)

    # Ordered first/second factor Q[a,b] = w[a]w[b] / ((1-w[a])
    # * (1-w[a]-w[b])). The diagonal is not a legal ordered pair.
    wa=w[:,:,None]; wb=w[:,None,:]
    q=wa*wb/((1-wa)*(1-wa-wb))
    mask=~np.eye(n,dtype=bool)
    q=np.where(mask[None,:,:],q,0.0)
    total=q.sum(axis=(1,2))
    row=q.sum(axis=2); col=q.sum(axis=1)
    r3=w*(total[:,None]-row-col)
    return w,w+r2,w+r2+r3

def _pl_marginals(weights):
    w=np.asarray(weights,dtype=float)
    if w.ndim!=1: raise ValueError("positive finite race weights required")
    a,b,c=_pl_marginals_batch(w)
    return a[0],b[0],c[0]

def wide_pair_matrix(weights):
    """P(i and j both finish Top3) for every pair, without Python triple loops."""
    w=np.asarray(weights,dtype=float)
    if w.ndim!=1 or len(w)<3 or not np.isfinite(w).all() or (w<=0).any(): raise ValueError("positive finite race weights required")
    w=w/w.sum(); n=len(w); _,p3=_permutation_indices(n)
    a,b,c=p3[:,0],p3[:,1],p3[:,2]
    p=w[a]*w[b]/(1-w[a])*w[c]/(1-w[a]-w[b])
    out=np.zeros((n,n),dtype=float)
    for u,v in ((a,b),(a,c),(b,c)):
        np.add.at(out,(u,v),p)
        np.add.at(out,(v,u),p)
    return out

def fit_race_weights(win,top2,top3,*,iterations=300,lr=.08):
    targets=[np.asarray(x,dtype=float) for x in (win,top2,top3)]; n=len(targets[0])
    if any(x.shape!=(n,) or not np.isfinite(x).all() for x in targets) or n<3: raise ValueError("aligned finite marginals required")
    if (targets[0]<=0).any() or abs(targets[0].sum()-1)>1e-5: raise ValueError("win probabilities must sum to one")
    if np.any(targets[1]<targets[0]-1e-8) or np.any(targets[2]<targets[1]-1e-8): raise ValueError("marginals must be monotone")
    z=np.log(np.clip(targets[0],1e-9,None)); z-=z.mean(); eps=1e-5
    def losses(v):
        vv=np.asarray(v,dtype=float)
        if vv.ndim==1: vv=vv[None,:]
        ww=np.exp(vv-vv.max(axis=1,keepdims=True))
        a,b,c=_pl_marginals_batch(ww)
        return 2*np.mean((a-targets[0])**2,axis=1)+np.mean((b-targets[1])**2,axis=1)+np.mean((c-targets[2])**2,axis=1)
    base=losses(z)[0]
    for _ in range(iterations):
        pert=np.repeat(z[None,:],n,axis=0); pert[np.arange(n),np.arange(n)]+=eps
        grad=(losses(pert)-base)/eps
        candidate=z-lr*grad; candidate-=candidate.mean()
        candidate_loss=losses(candidate)[0]
        if candidate_loss<=base:
            z=candidate; base=candidate_loss
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
