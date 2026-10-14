import XCTest
import UIKit

final class PhoneNavigationUITests: XCTestCase {
    @MainActor
    func testWideAxisEvaluationAndRefund() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["KEIBAAI_UI_FIXTURE"] = "1"
        app.launchEnvironment["KEIBAAI_UI_TICKET"] = "1"
        app.launchEnvironment["KEIBAAI_UI_AXIS"] = "1"
        app.launch()
        XCTAssertTrue(app.descendants(matching:.any).matching(identifier:"race-card-202605040601").firstMatch.waitForExistence(timeout:20))
        let tabs = app.scrollViews["main-tabs"]
        let menu = app.buttons["main-tab-馬券プラン"]
        for _ in 0..<4 { if menu.isHittable { break }; tabs.swipeLeft() }
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
        let race = app.descendants(matching: .any).matching(identifier:"race-card-202605040601").firstMatch
        XCTAssertTrue(race.waitForExistence(timeout:20))
        let tabs = app.scrollViews["main-tabs"]
        let menu = app.buttons["main-tab-馬券プラン"]
        for _ in 0..<4 { if menu.isHittable { break }; tabs.swipeLeft() }
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
        for value in ["22.6%", "22.4%", "35.8%", "100.0%", "0.99", "78.7", "41.2%", "57.4%"] {
            let label = app.staticTexts.matching(NSPredicate(format: "label == %@", value)).firstMatch
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
        let tabs = app.scrollViews["main-tabs"]
        let guide = app.buttons["main-tab-凡例"]
        for _ in 0..<4 {
            if guide.exists && guide.isHittable { break }
            tabs.swipeLeft()
        }
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
        let predicted = app.descendants(matching: .any).matching(identifier: "race-card-202605040601").firstMatch
        XCTAssertTrue(predicted.waitForExistence(timeout: 20), "fixture race list loaded")
        predicted.tap()
        let back = app.buttons["back-to-list"]
        XCTAssertTrue(back.waitForExistence(timeout: 5), "tap opens the detail column on iPhone")
        XCTAssertTrue(app.navigationBars["東京 1R"].exists)
        XCTAssertTrue(app.staticTexts["操作確認レース"].exists)
        back.tap()
        XCTAssertTrue(predicted.waitForExistence(timeout: 5))
        // Repeating the same selection catches a stale compact-column binding.
        predicted.tap()
        XCTAssertTrue(back.waitForExistence(timeout: 5))
        back.tap()
        let missing = app.descendants(matching: .any).matching(identifier: "race-card-202605040602").firstMatch
        XCTAssertTrue(missing.waitForExistence(timeout: 5))
        missing.tap()
        XCTAssertTrue(back.waitForExistence(timeout: 5))
        XCTAssertTrue(app.navigationBars["東京 2R"].exists)
        XCTAssertTrue(app.staticTexts["未予測の操作確認"].exists)
        back.tap()
        let tabs = app.scrollViews["main-tabs"]
        let guide = app.buttons["main-tab-凡例"]
        for _ in 0..<4 {
            if guide.exists && guide.isHittable { break }
            tabs.swipeLeft()
        }
        XCTAssertTrue(guide.isHittable)
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
}
