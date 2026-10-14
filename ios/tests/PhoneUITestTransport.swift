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
        if ProcessInfo.processInfo.environment["KEIBAAI_UI_LAYOUT"] == "1" {
            sample["raceName"] = "オクトーバー"
            sample["startTime"] = "15:30"
            var horses = sample["horses"] as! [[String: Any]]
            for index in horses.indices { horses[index]["aiProbability"] = 1.0 }
            horses[0]["name"] = "アスクセクシーモア"
            horses[0]["marketProbability"] = 22.6
            horses[0]["aiProbability"] = 22.4
            horses[0]["aiTop2Probability"] = 35.8
            horses[0]["aiTop3Probability"] = 100.0
            horses[0]["marketTop2Probability"] = 41.2
            horses[0]["marketTop3Probability"] = 57.4
            horses[0]["aiMarketRatio"] = 0.99
            horses[0]["odds"] = 3.5
            horses[0]["expectedIndex"] = 78.7
            sample["horses"] = horses
        }
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
