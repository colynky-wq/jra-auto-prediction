#!/usr/bin/env python3
"""Fail-closed validation of the app's derived research display output."""
import json


def main():
    with open('AppResearchData.json', encoding='utf-8') as f:
        payload = json.load(f)
    assert payload['status'] == 'research_display_only'
    assert payload['stakeSizingEnabled'] is False
    assert payload['formalSignalsChanged'] is False
    assert payload['evaluation2025']['rows'] > 0
    assert payload['formationValidation']['status'] == 'research_reference_only'
    assert payload['formationValidation']['paceForecastValidated'] is False
    for race in payload['races']:
        formation = race['formation']
        assert formation['paceForecast']['status'] == 'unavailable'
        if formation['status'] == 'reference_order':
            assert formation['coveragePct'] >= 80
            assert len(formation['orderedHorseNumbers']) == formation['knownHorses']
            assert set(formation['orderedHorseNumbers']) <= {h['number'] for h in race['horses']}
        else:
            assert formation['orderedHorseNumbers'] == []
        for horse in race['horses']:
            stat = horse['styleTendency']
            if stat:
                assert stat['latestHistoryDate'] < race['raceDate']
                assert 1 <= stat['sampleRaces'] <= 6
                assert abs(sum(stat['distributionPct'].values()) - 100) < 0.3
            position = horse['initialPositionHistory']
            if position:
                assert position['latestHistoryDate'] < race['raceDate']
                assert 0 <= position['meanRelativePosition'] <= 1
    print('APP_RESEARCH_DISPLAY_OK')


if __name__ == '__main__':
    main()
