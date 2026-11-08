"""Static safety contract for the iOS single research-choice display.

Not a Swift compiler or simulator substitute.
"""
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "KeibaAI" / "KeibaAI" / "KeibaAIBetResearchView.swift"


class SingleResearchChoiceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text(encoding="utf-8")
        start = cls.source.index("private var singleResearchChoices:")
        end = cls.source.index("private var singleResearchChoiceSection:", start)
        cls.selector = cls.source[start:end]

    def test_present_in_view(self):
        self.assertIn("singleResearchChoiceSection", self.source)
        self.assertIn("singleResearchChoiceSection\n\n                    earlyPredictionSection", self.source)

    def test_only_saved_research_not_formal(self):
        for clause in ("!data.formalRecommendationsEnabled", "!data.autoBetEnabled",
                       "$0.isSaved && $0.hasRecord", "$0.recordedAtJST != nil"):
            with self.subTest(clause=clause):
                self.assertIn(clause, self.selector)

    def test_invalid_records_excluded(self):
        for state in ("invalid", "failed", "missing_record", "held",
                      "entrant_changed", "dead_heat_excluded"):
            self.assertIn('"' + state + '"', self.selector)
        self.assertIn('$0.betType != "win5"', self.selector)
        self.assertIn('$0.betType != "support"', self.selector)

    def test_one_choice_per_race(self):
        self.assertIn("Dictionary(grouping: eligible, by: \\.raceID)", self.selector)
        self.assertIn("}).first", self.selector)
        self.assertIn("ForEach(singleResearchChoices.indices, id: \\.self)", self.source)

    def test_visible_research_disclaimer_and_provenance(self):
        for phrase in ("見送り・検証不足", "購入推奨ではありません",
                       "正式T-10の凍結買い目とは別", "choice.recordedAt",
                       "choice.candidate.probabilityText"):
            self.assertIn(phrase, self.source)


if __name__ == "__main__":
    unittest.main()
