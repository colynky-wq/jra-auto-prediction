import XCTest

final class PhoneNavigationUITests: XCTestCase {
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
