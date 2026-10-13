// Combined with the unchanged private production store by build_recording_store_checks.py.
// All URLSession.shared requests are intercepted; no public data is fetched or written.

private final class RecordingTransportProtocol: URLProtocol {
    enum Plan {
        case http(Int, Data)
        case failure(URLError.Code)
        case hold
    }
    static let lock = NSLock()
    static var plan = Plan.http(500, Data())
    static var requests: [URLRequest] = []
    static var held: RecordingTransportProtocol?
    static var stopCount = 0

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        Self.lock.lock()
        Self.requests.append(request)
        let selected = Self.plan
        if case .hold = selected { Self.held = self }
        Self.lock.unlock()
        deliver(selected)
    }
    override func stopLoading() {
        Self.lock.lock(); defer { Self.lock.unlock() }
        Self.stopCount += 1
        if Self.held === self { Self.held = nil }
    }
    func deliver(_ selected: Plan) {
        switch selected {
        case let .http(status, body):
            client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: status,
                                httpVersion: nil, headerFields: ["Content-Type": "application/json"])!,
                                cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: body)
            client?.urlProtocolDidFinishLoading(self)
        case let .failure(code):
            client?.urlProtocol(self, didFailWithError: URLError(code))
        case .hold: break
        }
    }
    static func set(_ selected: Plan) {
        lock.lock(); defer { lock.unlock() }
        plan = selected
    }
    static var requestCount: Int {
        lock.lock(); defer { lock.unlock() }
        return requests.count
    }
    static var stops: Int {
        lock.lock(); defer { lock.unlock() }
        return stopCount
    }
    static var lastRequest: URLRequest? {
        lock.lock(); defer { lock.unlock() }
        return requests.last
    }
    static func finishHeld(_ body: Data) {
        lock.lock()
        let pending = held
        held = nil
        lock.unlock()
        guard let pending else { fatalError("expected a held HTTP request") }
        pending.deliver(.http(200, body))
    }
}

@main
private struct RecordingStatusStoreChecks {
    static func check(_ condition: Bool, _ message: String) {
        if !condition { fatalError(message) }
    }
    @MainActor
    static func waitFor(_ condition: () -> Bool, _ message: String) async throws {
        for _ in 0..<1000 {
            if condition() { return }
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        fatalError(message)
    }
    static func payload(_ marker: String) throws -> Data {
        let states = [("saved", "予測保存済み", "結果待ち", false),
                      ("held", "履歴不足で保留", "結果待ち", false),
                      ("failed", "保存失敗", "未照合", true)]
        let rows: [[String: Any]] = states.enumerated().map { i, state in
            ["raceID": "fixture-\(i)", "raceDate": "2026-10-17", "venue": "試験", "raceNumber": i + 1,
             "startTime": "10:00", "recordingState": state.0, "recordingLabel": state.1,
             "resultLabel": state.2, "reason": "通信試験専用", "issues": [String](),
             "needsAttention": state.3]
        }
        return try JSONSerialization.data(withJSONObject: ["generatedAtJST": marker,
            "recordingStartDate": "2026-10-13", "counts": ["totalRaces": 3, "saved": 1, "held": 1, "failed": 1],
            "races": rows, "emptyMessage": "", "warnings": [String]()])
    }

    @MainActor
    static func main() async throws {
        // A broken cancellation must fail CI promptly instead of hanging the job.
        DispatchQueue.global().asyncAfter(deadline: .now() + 60) {
            fatalError("recording refresh fixture exceeded 60 seconds")
        }
        check(URLProtocol.registerClass(RecordingTransportProtocol.self), "fixture protocol must be registered")
        defer { URLProtocol.unregisterClass(RecordingTransportProtocol.self) }
        let store = KeibaAIRecordingStatusStore()
        check(store.data == nil && !store.isLoading && store.errorMessage == nil, "initial state")
        RecordingTransportProtocol.set(.http(200, try payload("first")))
        await store.load()
        check(store.data?.generatedAtJST == "first" && !store.isLoading && store.errorMessage == nil, "first success")
        check(store.data?.races.map(\.recordingState) == ["saved", "held", "failed"], "recorded states stay distinct")
        let request = RecordingTransportProtocol.lastRequest!
        check(request.url?.host == "raw.githubusercontent.com" && request.url?.path.hasSuffix("/PaceRecordingStatus.json") == true, "actual production URL")
        check(URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.contains(where: { $0.name == "t" && Double($0.value ?? "") != nil }) == true, "cache-busting timestamp")
        check(request.cachePolicy == .reloadIgnoringLocalCacheData && request.timeoutInterval == 20, "actual request policy and timeout")
        print("RECORDING_REFRESH_CASE_OK: initial_success_and_request")

        for status in [404, 500, 503] {
            RecordingTransportProtocol.set(.http(status, try payload("must-not-replace")))
            await store.load()
            check(store.data?.generatedAtJST == "first" && store.data?.races.count == 3, "HTTP failure keeps previous records")
            check(store.errorMessage != nil && !store.isLoading, "HTTP failure is visible and can retry")
        }
        print("RECORDING_REFRESH_CASE_OK: http_failures_keep_previous")

        for body in [Data(), Data("{unfinished".utf8), Data("null".utf8), Data("{\"races\":[]}".utf8)] {
            RecordingTransportProtocol.set(.http(200, body))
            await store.load()
            check(store.data?.generatedAtJST == "first" && store.errorMessage != nil && !store.isLoading, "bad JSON keeps previous records and reports failure")
        }
        print("RECORDING_REFRESH_CASE_OK: invalid_payloads_keep_previous")

        for code in [URLError.Code.timedOut, .notConnectedToInternet, .networkConnectionLost] {
            RecordingTransportProtocol.set(.failure(code))
            await store.load()
            check(store.data?.generatedAtJST == "first" && store.errorMessage != nil && !store.isLoading, "transport failure keeps previous records")
        }
        print("RECORDING_REFRESH_CASE_OK: network_failures_keep_previous")

        RecordingTransportProtocol.set(.http(200, try payload("recovered")))
        await store.load()
        check(store.data?.generatedAtJST == "recovered" && store.errorMessage == nil && !store.isLoading, "retry replaces with valid latest records and clears error")
        check(RecordingTransportProtocol.lastRequest?.url != request.url, "refresh uses a new timestamp")
        print("RECORDING_REFRESH_CASE_OK: retry_recovery")

        let before = RecordingTransportProtocol.requestCount
        RecordingTransportProtocol.set(.hold)
        let pending = Task { await store.load() }
        try await waitFor({ RecordingTransportProtocol.requestCount == before + 1 }, "held request never started")
        check(store.isLoading && store.data?.generatedAtJST == "recovered", "loading retains visible records")
        await store.load()
        check(RecordingTransportProtocol.requestCount == before + 1, "duplicate refresh does not issue another request")
        RecordingTransportProtocol.finishHeld(try payload("after-held"))
        await pending.value
        check(store.data?.generatedAtJST == "after-held" && !store.isLoading, "one held refresh completes normally")
        print("RECORDING_REFRESH_CASE_OK: duplicate_refresh_guard")

        let previousStops = RecordingTransportProtocol.stops
        let beforeCancel = RecordingTransportProtocol.requestCount
        RecordingTransportProtocol.set(.hold)
        let cancelled = Task { await store.load() }
        try await waitFor({ RecordingTransportProtocol.requestCount == beforeCancel + 1 }, "cancel request never started")
        cancelled.cancel()
        await cancelled.value
        try await waitFor({ RecordingTransportProtocol.stops > previousStops }, "cancel did not stop the transport")
        check(store.data?.generatedAtJST == "after-held" && store.errorMessage == nil && !store.isLoading, "cancel preserves previous records without error and releases loading")
        RecordingTransportProtocol.set(.http(200, try payload("after-cancel")))
        await store.load()
        check(store.data?.generatedAtJST == "after-cancel" && store.errorMessage == nil && !store.isLoading, "store can refresh after cancel")
        print("RECORDING_REFRESH_CASE_OK: cancellation_and_next_refresh")

        let empty = KeibaAIRecordingStatusStore()
        RecordingTransportProtocol.set(.http(503, Data()))
        await empty.load()
        check(empty.data == nil && empty.errorMessage != nil && !empty.isLoading, "initial failure cannot become a successful empty list")
        print("RECORDING_REFRESH_CASE_OK: initial_failure_is_not_zero")
        print("RECORDING_REFRESH_OK: unchanged production store, intercepted shared session, failure preservation, duplicate guard, cancellation")
    }
}
