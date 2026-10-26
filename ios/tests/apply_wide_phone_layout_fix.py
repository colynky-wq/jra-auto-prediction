from pathlib import Path

SOURCE = Path("ios/KeibaAI/KeibaAI/KeibaAI_NewDesignView.swift")
TESTS = Path("ios/tests/PhoneNavigationUITests.swift")


def patch_source() -> None:
    text = SOURCE.read_text(encoding="utf-8")

    tab_start = text.index("    private var detailTabBar: some View {")
    tab_end = text.index("    private var predictionSection: some View {", tab_start)
    tab = text[tab_start:tab_end]
    if "detail-tabs" not in tab:
        button_marker = "                    .buttonStyle(.plain)\n"
        assert button_marker in tab
        tab = tab.replace(
            button_marker,
            button_marker + '                    .accessibilityIdentifier("detail-tab-\\(tab.rawValue)")\n',
            1,
        )
        scroll_close = "            }\n        }\n    }\n\n"
        assert scroll_close in tab
        tab = tab.replace(
            scroll_close,
            '            }\n        }\n        .accessibilityIdentifier("detail-tabs")\n    }\n\n',
            1,
        )
        text = text[:tab_start] + tab + text[tab_end:]

    wide_start = text.index("    private var wideSection: some View {")
    wide_end = text.index("    private var paceSection: some View {", wide_start)
    wide = text[wide_start:wide_end]
    if "wide-evaluation-table-live" not in wide:
        table_open = "                VStack(spacing: 0) {\n"
        assert wide.count(table_open) == 3, wide.count(table_open)
        wide = wide.replace(
            table_open,
            "                ScrollView(.horizontal, showsIndicators: true) {\n                    VStack(spacing: 0) {\n",
        )
        background = "                .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 18))"
        assert wide.count(background) == 3, wide.count(background)
        for identifier in ("live", "validated", "probability"):
            wide = wide.replace(
                background,
                background
                + "\n                }\n"
                + f'                .accessibilityIdentifier("wide-evaluation-table-{identifier}")',
                1,
            )
        text = text[:wide_start] + wide + text[wide_end:]

    SOURCE.write_text(text, encoding="utf-8")


def patch_tests() -> None:
    tests = TESTS.read_text(encoding="utf-8")
    marker = "    func testWideEvaluationStaysInsidePhoneViewport() throws {"
    if marker in tests:
        return

    insertion = r'''

    @MainActor
    func testWideEvaluationStaysInsidePhoneViewport() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["KEIBAAI_UI_FIXTURE"] = "1"
        app.launchEnvironment["KEIBAAI_UI_TICKET"] = "1"
        app.launchEnvironment["KEIBAAI_UI_LAYOUT"] = "1"
        app.launch()

        let predicted = app.descendants(matching: .any)
            .matching(identifier: "race-card-202605040601").firstMatch
        XCTAssertTrue(predicted.waitForExistence(timeout: 20))
        predicted.tap()

        let title = app.staticTexts["race-detail-name"]
        XCTAssertTrue(title.waitForExistence(timeout: 5))
        XCTAssertEqual(title.label, "オクトーバー")

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

        let detail = app.scrollViews["race-detail"]
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
'''
    close = tests.rfind("\n}")
    assert close != -1
    tests = tests[:close] + insertion + tests[close:]
    TESTS.write_text(tests, encoding="utf-8")


if __name__ == "__main__":
    patch_source()
    patch_tests()
