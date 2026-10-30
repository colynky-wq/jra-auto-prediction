from pathlib import Path
from types import SimpleNamespace

import pytest

from live_bundle_publisher import Outbox, PublishError
from live_recording_resume import research_sidecars_complete


class DummyGit:
    def run(self,*args,**kwargs):
        if args[:2] == ('rev-parse','HEAD'):
            return SimpleNamespace(stdout=(b'0'*40))
        raise AssertionError(args)


def _outbox(tmp_path):
    root=tmp_path/'root'; root.mkdir()
    folder=tmp_path/'outbox'
    return Outbox(root,folder,'20261101',git=DummyGit(),today=lambda:'2026-11-01')


def test_ticket_shadow_and_formal_wide_are_remote_immutable(tmp_path):
    box=_outbox(tmp_path)
    local=b'{"value":1}\n'; same=bytes(local); different=b'{"value":2}\n'
    paths=(
        'reports/20261101/ticket_shadow_v1/prediction/202605040111.json',
        'reports/20261101/ticket_shadow_v1/manifest/202605040111.json',
        'reports/20261101/wide_odds_live10/202605040111.json',
    )
    for path in paths:
        assert box.allowed(path)
        assert box.bytes_for_remote(path,local,None)==local
        assert box.bytes_for_remote(path,local,same)==local
        with pytest.raises(PublishError,match='既存の発走前記録と競合'):
            box.bytes_for_remote(path,local,different)


def _legacy_sidecars(root: Path,date8: str,rid: str):
    day=root/'reports'/date8
    for kind in ('win','place','frame_quinella','quinella','exacta'):
        p=day/'all_bet_v1'/kind/(rid+'.json'); p.parent.mkdir(parents=True,exist_ok=True); p.write_text('{}')
    for kind in ('trio','trifecta'):
        p=day/'triple_v1'/kind/(rid+'.json'); p.parent.mkdir(parents=True,exist_ok=True); p.write_text('{}')
    p=day/'wide_axis_v1'/(rid+'.json'); p.parent.mkdir(parents=True,exist_ok=True); p.write_text('{}')
    return day


def test_ticket_shadow_completeness_gate_starts_november_2026(tmp_path):
    rid='202605040111'
    _legacy_sidecars(tmp_path,'20261031',rid)
    assert research_sidecars_complete(tmp_path,'20261031',rid)

    day=_legacy_sidecars(tmp_path,'20261101',rid)
    assert not research_sidecars_complete(tmp_path,'20261101',rid)
    for folder in ('prediction','manifest'):
        p=day/'ticket_shadow_v1'/folder/(rid+'.json'); p.parent.mkdir(parents=True,exist_ok=True); p.write_text('{}')
    assert research_sidecars_complete(tmp_path,'20261101',rid)
