#!/usr/bin/env python3
import copy, unittest
from race_simulator_v02 import HorseInput, simulate_race
from race_simulator_v02_adapter import simulate_current_keibaai_v02

class V02Tests(unittest.TestCase):
    def test_frames_are_monotonic_and_complete(self):
        horses=[HorseInput(1,"A","逃げ",independent_strength=1.05),HorseInput(2,"B","先行",independent_strength=1.0),HorseInput(3,"C","差し",independent_strength=.95)]
        r=simulate_race(horses,1600,"MID",40,77,.5)
        self.assertEqual(r["schemaVersion"],"race-simulator-v0.2")
        self.assertGreater(len(r["replay"]["frames"]),50)
        last={}
        for f in r["replay"]["frames"]:
            self.assertEqual(len(f["horses"]),3)
            for h in f["horses"]:
                self.assertGreaterEqual(h["progress"],last.get(h["horseNumber"],0)-1e-6); last[h["horseNumber"]]=h["progress"]
                self.assertTrue(0<=h["lane"]<=5); self.assertTrue(0<=h["progress"]<=1)
    def test_ai_market_odds_popularity_do_not_change_motion(self):
        race=[{"raceID":"R","venue":"東京","course":"芝1600m","horses":[{"number":1,"name":"A","horseKey":"A_2020","aiProbability":90,"marketProbability":90,"odds":1.1,"popularity":1},{"number":2,"name":"B","horseKey":"B_2020","aiProbability":10,"marketProbability":10,"odds":20,"popularity":2}]}]
        research={"races":[{"raceID":"R","horses":[{"number":1,"styleTendency":{"sampleRaces":4,"dominantStyle":"逃げ","distributionPct":{"逃げ":100}},"initialPositionHistory":{"meanRelativePosition":.1}},{"number":2,"styleTendency":{"sampleRaces":4,"dominantStyle":"差し","distributionPct":{"差し":100}},"initialPositionHistory":{"meanRelativePosition":.8}}]}]}
        profiles={"profiles":{}}
        flipped=copy.deepcopy(race); flipped[0]["horses"][0].update(aiProbability=1,marketProbability=1,odds=100,popularity=2);flipped[0]["horses"][1].update(aiProbability=99,marketProbability=99,odds=1.01,popularity=1)
        a=simulate_current_keibaai_v02(race,research,profiles,"R",30,9,.5);b=simulate_current_keibaai_v02(flipped,research,profiles,"R",30,9,.5)
        self.assertEqual(a["horses"],b["horses"]);self.assertEqual(a["replay"],b["replay"]);self.assertNotEqual(a["comparisonWithPredictionAI"],b["comparisonWithPredictionAI"])
    def test_style_pressure_can_infer_fast_without_market(self):
        race=[{"raceID":"R","venue":"東京","course":"芝1600m","horses":[{"number":i,"name":str(i),"horseKey":f"{i}_2020","aiProbability":10} for i in range(1,7)]}]
        horses=[]
        for i in range(1,7): horses.append({"number":i,"styleTendency":{"sampleRaces":4,"dominantStyle":"逃げ" if i<=4 else "先行","distributionPct":{"逃げ":100 if i<=4 else 0,"先行":0 if i<=4 else 100}},"initialPositionHistory":{"meanRelativePosition":.1 if i<=4 else .25}})
        result=simulate_current_keibaai_v02(race,{"races":[{"raceID":"R","horses":horses}]},{"profiles":{}},"R",10,3,.5)
        self.assertEqual(result["pace"],"FAST");self.assertEqual(result["paceSource"],"style-pressure-v0.2")
if __name__=="__main__":unittest.main()
