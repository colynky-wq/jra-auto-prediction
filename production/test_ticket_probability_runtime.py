from itertools import permutations
import numpy as np
from ticket_probability_runtime import distributions, fit_race_weights, _pl_marginals, _pl_marginals_batch


def _legacy_pl_marginals(weights):
    """Original scalar implementation retained as the mathematical oracle."""
    w=np.asarray(weights,dtype=float)
    w=w/w.sum(); n=len(w); r2=np.zeros(n); r3=np.zeros(n)
    for a,b in permutations(range(n),2):
        r2[b]+=w[a]*w[b]/(1-w[a])
    for a,b,c in permutations(range(n),3):
        r3[c]+=w[a]*w[b]/(1-w[a])*w[c]/(1-w[a]-w[b])
    return w,w+r2,w+r2+r3


def test_vectorized_marginals_match_legacy():
    rng=np.random.default_rng(20261031)
    for n in (3,5,8,12,18):
        rows=[]
        for _ in range(3):
            w=rng.lognormal(mean=0.0,sigma=1.0,size=n); rows.append(w)
            old=_legacy_pl_marginals(w); new=_pl_marginals(w)
            for a,b in zip(old,new): assert np.allclose(a,b,rtol=1e-13,atol=1e-14)
        batched=_pl_marginals_batch(np.asarray(rows))
        for r,w in enumerate(rows):
            old=_legacy_pl_marginals(w)
            for a,b in zip(old,batched): assert np.allclose(a,b[r],rtol=1e-13,atol=1e-14)


def _legacy_fit(win,top2,top3,*,iterations=40,lr=.08):
    targets=[np.asarray(x,dtype=float) for x in (win,top2,top3)]; n=len(targets[0]); z=np.log(np.clip(targets[0],1e-9,None)); z-=z.mean(); eps=1e-5
    def loss(v):
        a,b,c=_legacy_pl_marginals(np.exp(v-v.max()))
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


def test_batched_finite_difference_fit_matches_legacy():
    # Use valid coherent PL marginals. The previous fixture mixed Win from one
    # random distribution with Top2/Top3 from another, which can violate the
    # runtime's required per-horse monotonicity (Win <= Top2 <= Top3) before the
    # optimizer is even exercised.
    rng=np.random.default_rng(314159)
    for n in (4,7,12):
        win=rng.dirichlet(np.ones(n)*2)
        _,t2,t3=_legacy_pl_marginals(win)
        # Shift valid targets slightly upward while preserving monotonicity so
        # the optimizer has non-zero work rather than only testing a fixed point.
        room2=np.maximum(0.0,t3-t2)
        t2=t2+0.07*room2
        room3=np.maximum(0.0,1.0-t3)
        t3=t3+0.04*room3
        old=_legacy_fit(win,t2,t3,iterations=40)
        new=fit_race_weights(win,t2,t3,iterations=40)
        assert np.allclose(old,new,rtol=2e-10,atol=2e-12)


def test_joint_probability_invariants():
    nums=[1,2,3,4,5]; win=np.array([.35,.25,.18,.13,.09])
    _,t2,t3=_pl_marginals(np.array([.34,.26,.17,.14,.09]))
    d=distributions(nums,win,t2,t3)
    assert abs(sum(d['quinella'].values())-1)<1e-8
    assert abs(sum(d['exacta'].values())-1)<1e-8
    assert abs(sum(d['trio'].values())-1)<1e-8
    assert abs(sum(d['trifecta'].values())-1)<1e-8
    assert all(0 < p <= 1 for p in d['wide'].values())
    assert all(abs(d['quinella'][tuple(sorted(k))] - (d['exacta'][k] + d['exacta'][(k[1],k[0])])) < 1e-10 for k in [(1,2),(1,3),(2,4)])


def test_fit_is_deterministic_and_positive():
    w=np.array([.4,.3,.2,.1]); a,b,c=_pl_marginals(w)
    x=fit_race_weights(a,b,c,iterations=40); y=fit_race_weights(a,b,c,iterations=40)
    assert np.allclose(x,y)
    assert np.all(x>0) and abs(x.sum()-1)<1e-12
