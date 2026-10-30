import numpy as np
from ticket_probability_runtime import distributions, fit_race_weights, _pl_marginals


def test_joint_probability_invariants():
    nums=[1,2,3,4,5]
    win=np.array([.35,.25,.18,.13,.09])
    # Coherent target marginals from a different PL strength vector.
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
    x=fit_race_weights(a,b,c,iterations=40)
    y=fit_race_weights(a,b,c,iterations=40)
    assert np.allclose(x,y)
    assert np.all(x>0) and abs(x.sum()-1)<1e-12
