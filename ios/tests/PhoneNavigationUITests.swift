import XCTest
import UIKit

final class PhoneNavigationUITests: XCTestCase {
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
