import XCTest
import UIKit

final class PhoneNavigationUITests: XCTestCase {
    @MainActor
    func testSimulatorRecordingLearningAndFailureDisplay() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["KEIBAAI_UI_FIXTURE"] = "1"
        app.launchEnvironment["KEIBAAI_UI_SIMULATOR"] = "1"
        app.launch()
        let tab = app.tabBars.buttons["展開シミュ"]
        XCTAssertTrue(tab.waitForExistence(timeout: 10)); tab.tap()
        let link = app.buttons["sim-learning-link"]
        XCTAssertTrue(link.waitForExistence(timeout: 15)); link.tap()
        XCTAssertTrue(app.staticTexts["別レースで候補を検証中"].waitForExistence(timeout: 15))
        let list = app.collectionViews.firstMatch
        for _ in 0..<8 {
            if app.descendants(matching: .any).matching(identifier: "sim-condition-surface:dirt").firstMatch.isHittable { break }
            list.swipeUp()
        }
        let condition = app.descendants(matching: .any).matching(identifier: "sim-condition-surface:dirt").firstMatch
        XCTAssertTrue(condition.isHittable)
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "データ不足・判断保留")).firstMatch.exists)
        let failed = app.descendants(matching: .any).matching(identifier: "sim-recording-202605040602").firstMatch
        for _ in 0..<6 { if failed.isHittable { break }; list.swipeUp() }
        XCTAssertTrue(failed.isHittable)
        XCTAssertTrue(app.staticTexts["保存失敗"].exists)
        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = "simulator-recording-learning"; shot.lifetime = .keepAlways; add(shot)
        app.terminate()
        app.launchEnvironment["KEIBAAI_UI_SIM_OPS_ERROR"] = "1"; app.launch()
        XCTAssertTrue(tab.waitForExistence(timeout: 10)); tab.tap()
        XCTAssertTrue(link.waitForExistence(timeout: 15)); link.tap()
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "取得できませんでした")).firstMatch.waitForExistence(timeout: 15))
        XCTAssertFalse(app.staticTexts["別レースで候補を検証中"].exists)
        print("PHONE_SIMULATOR_OPERATIONS_OK: live verification pending, conditional insufficiency, recording failure and HTTP errors")
    }

    @MainActor
    func testSimulatorFramesPlaybackAndPendingRace() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["KEIBAAI_UI_FIXTURE"] = "1"
        app.launchEnvironment["KEIBAAI_UI_SIMULATOR"] = "1"
        app.launch()
        let tab = app.tabBars.buttons["展開シミュ"]
        XCTAssertTrue(tab.waitForExistence(timeout: 10))
        tab.tap()
        let ready = app.buttons["sim-race-202605040601"]
        XCTAssertTrue(ready.waitForExistence(timeout: 20))
        ready.tap()
        XCTAssertTrue(app.staticTexts["総合判断"].waitForExistence(timeout: 10))
        let judgment = app.descendants(matching: .any).matching(identifier: "sim-judgment-1").firstMatch
        XCTAssertTrue(judgment.label.contains("AI 1位"), "the prediction AI remains the primary ranking")

        let screen = app.scrollViews["sim-detail"]
        let play = app.buttons["sim-play-pause"]
        for _ in 0..<8 { if play.isHittable { break }; screen.swipeUp() }
        XCTAssertTrue(play.isHittable)
        let elapsed = app.staticTexts["sim-elapsed"]
        let start = elapsed.label
        play.tap()
        let advances = XCTNSPredicateExpectation(predicate: NSPredicate(format: "label != %@", start), object: elapsed)
        XCTAssertEqual(XCTWaiter.wait(for: [advances], timeout: 8), .completed, "play advances the real frame timeline")
        play.tap()
        XCTAssertEqual(play.label, "再生")
        let speeds = app.segmentedControls["sim-speed"]
        for _ in 0..<3 { if speeds.isHittable { break }; screen.swipeUp() }
        speeds.buttons["4×"].tap()
        XCTAssertTrue(speeds.buttons["4×"].isSelected)
        let seek = app.sliders["sim-seek"]
        seek.adjust(toNormalizedSliderPosition: 0.5)
        let times = elapsed.label.components(separatedBy: " / ")
        let current = try XCTUnwrap(Double(times[0]))
        let duration = try XCTUnwrap(Double(times[1].replacingOccurrences(of: "秒", with: "")))
        XCTAssertEqual(current / duration, 0.5, accuracy: 0.08)
        XCTAssertEqual(play.label, "再生", "seeking pauses playback")
        seek.adjust(toNormalizedSliderPosition: 0.0)
        let horse = app.buttons["sim-horse-1"]
        for _ in 0..<3 { if horse.isHittable { break }; screen.swipeDown() }
        XCTAssertTrue(horse.isHittable)
        XCTAssertTrue((horse.value as? String ?? "").contains("位置"))
        horse.tap()
        // SwiftUI exposes this one action as nested buttons with the same ID.
        // Scope to the chooser and use its first representation of that action.
        let choice = app.sheets["重なっている馬を選択"].buttons["sim-choose-horse-1"].firstMatch
        XCTAssertTrue(choice.waitForExistence(timeout: 5), "overlapping markers offer an explicit horse choice")
        let choiceShot = XCTAttachment(screenshot: app.screenshot())
        choiceShot.name = "simulator-overlap-picker"; choiceShot.lifetime = .keepAlways; add(choiceShot)
        choice.tap()
        let selected = app.descendants(matching: .any).matching(identifier: "sim-selected-horse").firstMatch
        for _ in 0..<3 { if selected.isHittable { break }; screen.swipeUp() }
        XCTAssertTrue(selected.label.contains("再生確認1"))
        XCTAssertTrue(selected.label.contains("m/s"))
        XCTAssertTrue(selected.label.contains("差"))
        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = "simulator-v02-frame-controls"; shot.lifetime = .keepAlways; add(shot)
        let reset = app.buttons["sim-reset"]
        for _ in 0..<3 { if reset.isHittable { break }; screen.swipeDown() }
        seek.adjust(toNormalizedSliderPosition: 0.6)
        reset.tap()
        XCTAssertTrue(elapsed.label.hasPrefix("0.0 /"))
        app.navigationBars.buttons.element(boundBy: 0).tap()
        XCTAssertTrue(ready.waitForExistence(timeout: 5))
        ready.tap()
        XCTAssertTrue(app.staticTexts["総合判断"].waitForExistence(timeout: 5), "the same race opens after returning")
        app.navigationBars.buttons.element(boundBy: 0).tap()
        app.buttons["sim-race-202605040602"].tap()
        XCTAssertTrue(app.staticTexts["v0.2生成待ち"].waitForExistence(timeout: 5))
        print("PHONE_SIMULATOR_V02_OK: real engine frames, AI-first judgment, play/pause, 4x, seek, overlapping horse choice, telemetry, reset, repeat navigation and pending data")
    }

    // The production landing screen intentionally shows only Grade B+ picks.
    // UI fixtures may not have a battle grade, so explicitly navigate to the
    // unfiltered race list before asserting the presence of fixture race rows.
    @MainActor
    private func showAllRaces(_ app: XCUIApplication) {
        let allRaces = app.buttons["全レース"].firstMatch
        XCTAssertTrue(allRaces.waitForExistence(timeout: 15), "full race list mode is selectable")
        allRaces.tap()
    }
    @MainActor
    private func openResearch(_ app:XCUIApplication) -> XCUIElement {
        app.launchEnvironment["KEIBAAI_UI_FIXTURE"]="1"
        app.launchEnvironment["KEIBAAI_UI_TICKET"]="1"
        app.launch()
        showAllRaces(app)
        XCTAssertTrue(app.descendants(matching:.any).matching(identifier:"race-card-202605040601").firstMatch.waitForExistence(timeout:20))
        let button = app.buttons["main-tab-研究"]
        XCTAssertTrue(button.waitForExistence(timeout: 10), "research tab is accessible")
        button.tap()
        let screen=app.scrollViews["bet-research-screen"]
        XCTAssertTrue(screen.waitForExistence(timeout:5));return screen
    }

    @MainActor
    func testAllBetResearchReferenceAndDecimals() throws {
        continueAfterFailure=false
        let app=XCUIApplication(),screen=openResearch(app)
        // The new one-ticket research section precedes the lazy bet-type grid.
        // Scroll the grid into the viewport before querying lazily realized buttons.
        let win5Button = app.buttons["bet-type-win5"]
        for _ in 0..<8 {
            if win5Button.exists { break }
            screen.swipeUp()
        }
        XCTAssertTrue(win5Button.waitForExistence(timeout:10))
        for kind in ["win","place","frame_quinella","quinella","wide","exacta","trio","trifecta","support","win5"] {
            XCTAssertTrue(app.buttons["bet-type-"+kind].exists,"every pool is selectable")
        }
        app.buttons["bet-type-trifecta"].tap()
        let candidate=app.staticTexts["bet-candidate-trifecta-010203"]
        for _ in 0..<6 { if candidate.isHittable { break };screen.swipeUp() }
        XCTAssertTrue(candidate.label.contains("アスクセクシーモア"))
        let percent=app.staticTexts.matching(NSPredicate(format:"label == %@","0.000123%")).firstMatch
        for _ in 0..<3 { if percent.isHittable { break };screen.swipeUp() }
        XCTAssertTrue(percent.isHittable,"tiny decimals are fully displayed")
        let shot=XCTAttachment(screenshot:app.screenshot());shot.name="all-bet-trifecta-full-names-decimals";shot.lifetime = .keepAlways;add(shot)
        for kind in ["support","win5","frame_quinella"] {
            let button=app.buttons["bet-type-"+kind]
            for _ in 0..<12 { if button.isHittable { break };screen.swipeDown() };button.tap()
            let label:XCUIElement
            if kind == "support" { label=app.staticTexts.matching(NSPredicate(format:"label CONTAINS %@","計200円の組合せ")).firstMatch }
            else if kind == "win5" { label=app.staticTexts.matching(NSPredicate(format:"identifier BEGINSWITH %@","bet-candidate-win5-")).firstMatch }
            else { label=app.staticTexts["公式出馬表の枠番号を確認できないため保留しています。"] }
            for _ in 0..<7 { if label.isHittable { break };screen.swipeUp() }
            XCTAssertTrue(label.isHittable)
            if kind == "win5" { XCTAssertEqual(label.label.components(separatedBy:"\n").count,5) }
            let shot=XCTAttachment(screenshot:app.screenshot());shot.name="all-bet-"+kind;shot.lifetime = .keepAlways;add(shot)
        }
        print("PHONE_ALL_BET_OK: ten pools, full names, tiny decimals, 200-yen support, five WIN5 legs and held reason")
    }

    @MainActor
    func testBetResearchEmptyAndFailureAreDistinct() throws {
        continueAfterFailure=false
        let app=XCUIApplication();app.launchEnvironment["KEIBAAI_UI_BET_EMPTY"]="1"
        let screen=openResearch(app)
        let empty=app.staticTexts["bet-research-empty"]
        for _ in 0..<6 { if empty.isHittable { break };screen.swipeUp() }
        XCTAssertTrue(empty.label.contains("まだ確認できません"));XCTAssertFalse(app.staticTexts["bet-research-error"].exists)
        let shot=XCTAttachment(screenshot:app.screenshot());shot.name="all-bet-zero-records";shot.lifetime = .keepAlways;add(shot)
        app.terminate();app.launchEnvironment["KEIBAAI_UI_BET_ERROR"]="1"
        _=openResearch(app)
        XCTAssertTrue(app.staticTexts["bet-research-error"].waitForExistence(timeout:10))
        XCTAssertFalse(app.staticTexts["bet-research-empty"].exists,"failed fetch is not a successful empty list")
        let errorShot=XCTAttachment(screenshot:app.screenshot());errorShot.name="all-bet-fetch-error";errorShot.lifetime = .keepAlways;add(errorShot)
        print("PHONE_ALL_BET_EMPTY_ERROR_OK")
    }
    @MainActor
    func testWideAxisEvaluationAndRefund() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["KEIBAAI_UI_FIXTURE"] = "1"
        app.launchEnvironment["KEIBAAI_UI_TICKET"] = "1"
        app.launchEnvironment["KEIBAAI_UI_AXIS"] = "1"
        app.launch()
        showAllRaces(app)
        XCTAssertTrue(app.descendants(matching:.any).matching(identifier:"race-card-202605040601").firstMatch.waitForExistence(timeout:20))
        let menu = app.buttons["main-tab-馬券"]
        XCTAssertTrue(menu.waitForExistence(timeout: 10), "ticket tab is accessible")
        menu.tap()
        let screen = app.scrollViews["reference-ticket-plan"]
        XCTAssertTrue(screen.waitForExistence(timeout:5))
        let count = app.staticTexts["axis-validation-count"]
        for _ in 0..<8 { if count.isHittable { break }; screen.swipeUp() }
        XCTAssertTrue(count.label.contains("記録 2件 / 結果照合 1件"))
        let axis = app.staticTexts["wide-axis-202605040601"]
        for _ in 0..<8 { if axis.isHittable { break }; screen.swipeUp() }
        XCTAssertTrue(axis.label.contains("穴軸 6番"))
        let refund = app.staticTexts["axis-refund-202605040601"]
        XCTAssertTrue(refund.label.contains("410円 / 仮合計 300円"))
        let pair = app.staticTexts["reference-pair-202605040601-1-6"]
        XCTAssertTrue(pair.exists)
        let shot = XCTAttachment(screenshot:app.screenshot())
        shot.name = "wide-axis-top3-and-minimum-refund";shot.lifetime = .keepAlways;add(shot)
        print("PHONE_WIDE_AXIS_OK: Top3 hole-axis, joint-pair evaluation, minimum single-hit refund and separate new-rule statistics")
    }

    @MainActor
    func testReferencePlanAndBudgetLimits() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["KEIBAAI_UI_FIXTURE"] = "1"
        app.launchEnvironment["KEIBAAI_UI_TICKET"] = "1"
        app.launch()
        showAllRaces(app)
        let race = app.descendants(matching: .any).matching(identifier:"race-card-202605040601").firstMatch
        XCTAssertTrue(race.waitForExistence(timeout:20))
        let menu = app.buttons["main-tab-馬券"]
        XCTAssertTrue(menu.waitForExistence(timeout: 10), "ticket tab is accessible")
        menu.tap()
        let screen = app.scrollViews["reference-ticket-plan"]
        XCTAssertTrue(screen.waitForExistence(timeout:5))
        for _ in 0..<4 { if app.buttons["バランス型"].isHittable { break }; screen.swipeUp() }
        app.buttons["バランス型"].tap()
        let count = app.staticTexts["reference-validation-count"]
        for _ in 0..<5 { if count.isHittable { break }; screen.swipeUp() }
        XCTAssertTrue(count.label.contains("4 / 300件"))
        XCTAssertTrue(app.staticTexts["reference-validation-return"].label.contains("80.0%"))
        let total = app.staticTexts["reference-day-total"]
        for _ in 0..<5 { if total.isHittable { break }; screen.swipeUp() }
        XCTAssertTrue(total.label.contains("300円"),"daily limit caps two venues together")
        for _ in 0..<7 { if app.buttons["安定型"].isHittable { break }; screen.swipeDown() }
        app.buttons["安定型"].tap()
        for _ in 0..<5 { if total.isHittable { break }; screen.swipeUp() }
        XCTAssertTrue(total.label.contains("200円"),"stable profile allocates only one per race")
        for _ in 0..<7 { if app.buttons["バランス型"].isHittable { break }; screen.swipeDown() }
        app.buttons["バランス型"].tap()
        let pair = app.staticTexts["reference-pair-202605040601-1-2"]
        for _ in 0..<5 { if pair.isHittable { break }; screen.swipeUp() }
        XCTAssertTrue(pair.isHittable)
        XCTAssertTrue(pair.label.contains("仮配分100円"))
        let shot = XCTAttachment(screenshot:app.screenshot())
        shot.name = "reference-ticket-plan-100yen"; shot.lifetime = .keepAlways; add(shot)
        for _ in 0..<8 { if app.buttons["予算を変更"].isHittable { break }; screen.swipeDown() }
        app.buttons["予算を変更"].tap()
        let field = app.textFields["1レースの予算を円で入力"]
        XCTAssertTrue(field.waitForExistence(timeout:5))
        field.tap()
        field.typeText(String(repeating:XCUIKeyboardKey.delete.rawValue,count:8)+"0")
        app.buttons["保存"].tap()
        for _ in 0..<5 { if total.isHittable { break }; screen.swipeUp() }
        XCTAssertTrue(total.label.contains("0円"),"zero budget recomputes all plans immediately")
        print("PHONE_REFERENCE_TICKET_OK: reference candidates, summary, cross-venue daily cap, zero-budget recompute")
    }

    @MainActor
    func testReadableRaceNameAndProbabilities() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["KEIBAAI_UI_FIXTURE"] = "1"
        app.launchEnvironment["KEIBAAI_UI_LAYOUT"] = "1"
        app.launch()
        showAllRaces(app)
        let predicted = app.descendants(matching: .any).matching(identifier: "race-card-202605040601").firstMatch
        XCTAssertTrue(predicted.waitForExistence(timeout: 20))
        predicted.tap()
        let title = app.staticTexts["race-detail-name"]
        XCTAssertTrue(title.waitForExistence(timeout: 5))
        XCTAssertEqual(title.label, "オクトーバー")
        XCTAssertLessThan(title.frame.height, UIFont.preferredFont(forTextStyle: .title1).lineHeight * 1.5,
                          "a normal race name fits one line, without sharing width with the status")
        let headerShot = XCTAttachment(screenshot: app.screenshot())
        headerShot.name = "race-header-full-width"
        headerShot.lifetime = .keepAlways
        add(headerShot)
        let detail = app.scrollViews["race-detail"]
        XCTAssertTrue(detail.exists)
        let reasons = app.buttons["理由を見る"]
        for _ in 0..<5 { if reasons.isHittable { break }; detail.swipeUp() }
        XCTAssertTrue(reasons.isHittable)
        reasons.tap()
        for value in ["22.6%", "22.4%", "35.8%", "100.0%", "0.99", "78.7", "41.2%", "57.4%"] {
            // The compact conclusion repeats the AI win probability. Inspect the
            // detailed metric rather than an offscreen copy in the conclusion.
            let label = app.staticTexts.matching(NSPredicate(format: "label == %@ AND identifier != %@", value, "conclusion-win-probability-1")).firstMatch
            for _ in 0..<8 {
                if label.exists && label.isHittable { break }
                detail.swipeUp()
            }
            XCTAssertTrue(label.exists && label.isHittable, "complete metric visible: \(value)")
            XCTAssertEqual(label.label, value)
            let font = UIFont.monospacedDigitSystemFont(ofSize: UIFont.preferredFont(forTextStyle: .subheadline).pointSize, weight: .bold)
            let needed = (value as NSString).size(withAttributes: [.font: font]).width
            XCTAssertGreaterThanOrEqual(label.frame.width, needed - 1, "decimal and percent fit without shrinking or ellipsis")
            XCTAssertGreaterThanOrEqual(label.frame.minX, app.frame.minX)
            XCTAssertLessThanOrEqual(label.frame.maxX, app.frame.maxX)
        }
        let valuesShot = XCTAttachment(screenshot: app.screenshot())
        valuesShot.name = "horse-probabilities-full-decimals"
        valuesShot.lifetime = .keepAlways
        add(valuesShot)
        app.buttons["back-to-list"].tap()
        let guide = app.buttons["見方・凡例"]
        XCTAssertTrue(guide.waitForExistence(timeout: 10), "guide entry is accessible")
        guide.tap()
        let full = app.scrollViews["full-guide"]
        for term in ["大穴の見方", "大穴とは", "オッズの妙味", "候補になる条件"] {
            let label = app.staticTexts.matching(NSPredicate(format: "label == %@", term)).firstMatch
            for _ in 0..<18 {
                if label.exists && label.isHittable { break }
                full.swipeUp()
            }
            XCTAssertTrue(label.exists && label.isHittable, "beginner guide explains \(term)")
        }
        let guideShot = XCTAttachment(screenshot: app.screenshot())
        guideShot.name = "longshot-beginner-explanation"
        guideShot.lifetime = .keepAlways
        add(guideShot)
        print("PHONE_LAYOUT_OK: full-width race name, eight complete metrics including 100.0%, beginner longshot guide")
    }

    @MainActor
    func testRaceDetailsReturnAndCompleteGuide() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["KEIBAAI_UI_FIXTURE"] = "1"
        app.launch()
        showAllRaces(app)
        let predicted = app.descendants(matching: .any).matching(identifier: "race-card-202605040601").firstMatch
        XCTAssertTrue(predicted.waitForExistence(timeout: 20), "fixture race list loaded")
        predicted.tap()
        let back = app.buttons["back-to-list"]
        XCTAssertTrue(back.waitForExistence(timeout: 5), "tap opens the detail column on iPhone")
        XCTAssertTrue(app.navigationBars["東京 1R"].exists)
        XCTAssertTrue(app.staticTexts["操作確認レース"].exists)
        back.tap()
        let predictedAgain = app.descendants(matching: .any).matching(identifier: "race-card-202605040601").firstMatch
        XCTAssertTrue(predictedAgain.waitForExistence(timeout: 5))
        // Repeating the same selection catches a stale compact-column binding. Re-query after
        // returning because an XCUIElement resolved before a NavigationSplitView column switch
        // can become stale while the newly rendered card is already visible.
        predictedAgain.tap()
        XCTAssertTrue(back.waitForExistence(timeout: 5))
        back.tap()
        let missing = app.descendants(matching: .any).matching(identifier: "race-card-202605040602").firstMatch
        XCTAssertTrue(missing.waitForExistence(timeout: 5))
        missing.tap()
        XCTAssertTrue(back.waitForExistence(timeout: 5))
        XCTAssertTrue(app.navigationBars["東京 2R"].exists)
        XCTAssertTrue(app.staticTexts["未予測の操作確認"].exists)
        back.tap()
        let guide = app.buttons["見方・凡例"]
        XCTAssertTrue(guide.waitForExistence(timeout: 10), "guide entry is accessible")
        guide.tap()
        XCTAssertTrue(app.navigationBars["凡例"].waitForExistence(timeout: 5))
        let full = app.scrollViews["full-guide"]
        XCTAssertTrue(full.exists, "complete existing guide, not three summary cards")
        for term in ["荒れ度", "勝負度", "主なAI表示", "AI÷市場", "ワイド評価", "予算設定"] {
            let label = app.staticTexts[term]
            for _ in 0..<18 {
                if label.exists && label.isHittable { break }
                full.swipeUp()
            }
            XCTAssertTrue(label.exists && label.isHittable, "guide includes \(term)")
        }
        back.tap()
        XCTAssertTrue(app.buttons["open-full-guide"].waitForExistence(timeout: 5))
        app.buttons["open-full-guide"].tap()
        XCTAssertTrue(app.navigationBars["凡例"].waitForExistence(timeout: 5))
        print("PHONE_NAVIGATION_OK: predicted/unpredicted detail, repeated return, full guide terms")
    }

    @MainActor
    func testWideEvaluationStaysInsidePhoneViewport() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["KEIBAAI_UI_FIXTURE"] = "1"
        app.launchEnvironment["KEIBAAI_UI_TICKET"] = "1"
        app.launchEnvironment["KEIBAAI_UI_LAYOUT"] = "1"
        app.launch()
        showAllRaces(app)

        let predicted = app.descendants(matching: .any)
            .matching(identifier: "race-card-202605040601").firstMatch
        XCTAssertTrue(predicted.waitForExistence(timeout: 20))
        predicted.tap()

        let title = app.staticTexts["race-detail-name"]
        XCTAssertTrue(title.waitForExistence(timeout: 5))
        XCTAssertEqual(title.label, "オクトーバー")

        let detail = app.scrollViews["race-detail"]
        let research = app.buttons["研究データを見る"]
        for _ in 0..<5 { if research.isHittable { break }; detail.swipeUp() }
        XCTAssertTrue(research.isHittable)
        research.tap()
        let tabs = app.scrollViews["detail-tabs"]
        XCTAssertTrue(tabs.waitForExistence(timeout: 5))
        let wide = app.buttons["detail-tab-ワイド評価"]
        for _ in 0..<6 {
            if wide.isHittable { break }
            tabs.swipeLeft()
        }
        XCTAssertTrue(wide.isHittable)
        wide.tap()

        XCTAssertGreaterThanOrEqual(title.frame.minX, app.frame.minX - 1)
        XCTAssertLessThanOrEqual(title.frame.maxX, app.frame.maxX + 1)

        let table = app.scrollViews["wide-evaluation-table-live"]
        for _ in 0..<8 {
            if table.exists && table.isHittable { break }
            detail.swipeUp()
        }
        XCTAssertTrue(table.exists && table.isHittable)
        XCTAssertGreaterThanOrEqual(table.frame.minX, app.frame.minX - 1)
        XCTAssertLessThanOrEqual(table.frame.maxX, app.frame.maxX + 1)

        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = "wide-phone-layout-contained"
        shot.lifetime = .keepAlways
        add(shot)
        print("PHONE_WIDE_LAYOUT_OK: wide table scrolls independently and race detail remains inside phone viewport")
    }

}
