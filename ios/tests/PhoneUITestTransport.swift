import Foundation
import UIKit

// Added to the app target ONLY in the disposable CI checkout, never in the ZIP.
enum KeibaAIUITestTransport {
    static func enableIfRequested() {
        guard ProcessInfo.processInfo.environment["KEIBAAI_UI_FIXTURE"] == "1" else { return }
        let data = NSDataAsset(name: "RaceData")!.data
        var sample = (try! JSONSerialization.jsonObject(with: data) as! [[String: Any]])[0]
        sample["raceID"] = "202605040601"
        sample["venue"] = "東京"
        sample["raceNumber"] = 1
        sample["startTime"] = "10:00"
        sample["raceName"] = "操作確認レース"
        PhoneFixtureProtocol.races = try! JSONSerialization.data(withJSONObject: [sample])
        PhoneFixtureProtocol.schedule = try! JSONSerialization.data(withJSONObject: [
            ["raceID": "202605040601", "venue": "東京", "raceNumber": 1, "startTime": "10:00",
             "raceName": "操作確認レース", "course": "芝2000m", "fieldSize": 18],
            ["raceID": "202605040602", "venue": "東京", "raceNumber": 2, "startTime": "10:30",
             "raceName": "未予測の操作確認", "course": "ダ1300m", "fieldSize": 16]
        ])
        URLProtocol.registerClass(PhoneFixtureProtocol.self)
    }
}

private final class PhoneFixtureProtocol: URLProtocol {
    static var races = Data()
    static var schedule = Data()
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let path = request.url!.path
        let data: Data
        let code: Int
        if path.hasSuffix("/RaceData.json") { data = Self.races; code = 200 }
        else if path.hasSuffix("/ScheduleData.json") { data = Self.schedule; code = 200 }
        else { data = Data(); code = 404 }
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: code,
                           httpVersion: nil, headerFields: ["Content-Type": "application/json"])!,
                           cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() { }
}
