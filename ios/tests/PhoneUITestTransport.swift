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
        if ProcessInfo.processInfo.environment["KEIBAAI_UI_TICKET"] == "1" {
            let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd"; f.timeZone = TimeZone(identifier: "Asia/Tokyo")
            let day = f.string(from: Date())
            UserDefaults.standard.set(3000, forKey: "KeibaAI.budget.perRaceYen.v1")
            UserDefaults.standard.set(300, forKey: "KeibaAI.budget.dailyLimitYen.v1")
            sample["raceDate"] = day
            sample["startTime"] = "10:00"
            var other = sample
            other["raceID"] = "202608040601"; other["venue"] = "京都"; other["startTime"] = "10:05"
            other["raceName"] = "仮配分の確認レース"
            PhoneFixtureProtocol.races = try! JSONSerialization.data(withJSONObject: [sample,other])
            let horses = sample["horses"] as! [[String:Any]]
            func pair(_ a: Int, _ b: Int, _ p: Double, _ odds: Double) -> [String:Any] {
                ["number1":a,"name1":horses[a-1]["name"]!,"number2":b,"name2":horses[b-1]["name"]!,
                 "marketWideProbability":p-1,"aiWideProbability":p,"probabilityEdgePct":1.0,
                 "aiMarketProbabilityRatio":1.1,"wideOddsLow":odds,"wideOddsHigh":odds+1,
                 "conservativeExpectedValue":(p/100*odds*10000).rounded()/10000]
            }
            let evaluation: [String:Any] = ["status":"odds_connected","oddsConnected":true,
                "capturedAtJST":day+" 09:50:00","pairs":[pair(1,2,30,4),pair(1,3,20,6),pair(2,3,10,12)]]
            let signals: [[String:Any]] = [sample,other].map { r in
                ["raceID":r["raceID"]!,"raceDate":day,"startTime":r["startTime"]!,
                 "eligible":true,"wideEvaluation":evaluation]
            }
            PhoneFixtureProtocol.signals = try! JSONSerialization.data(withJSONObject:
                ["schemaVersion":2,"signalVersion":"v2","generatedAtJST":day+" 09:50:00","formalSignals":true,"races":signals])
            var profiles = [String:Any]()
            for (key,n,ev) in [("安定型候補",1,1.1),("バランス型候補",2,1.1),("高期待値型候補",3,1.2)] {
                profiles[key] = ["rule":["topNPerRace":n,"thresholdEV":ev],"selectedRaces":4,
                    "selectedBets":4*n,"hits":1,"returnIndex":0.8,"status":"data_collecting"]
            }
            PhoneFixtureProtocol.shadow = try! JSONSerialization.data(withJSONObject: ["schemaVersion":1,
                "ruleVersion":"wide-ticket-shadow-v1-20261012","generatedAtJST":day+" 09:50:00",
                "validatedRacesAvailable":5,"autoBetEnabled":false,"moneyAllocationEnabled":false,
                "reviewGates":["minValidatedRaces":300,"minSelectedBets":150,"minOverallReturnIndex":1.0,
                               "minBootstrap95LowerReturnIndex":1.0,"minRecentHalfReturnIndex":0.95],"profiles":profiles])
        } else {
            PhoneFixtureProtocol.races = try! JSONSerialization.data(withJSONObject: [sample])
        }
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
    static var signals = Data()
    static var shadow = Data()
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let path = request.url!.path
        let data: Data
        let code: Int
        if path.hasSuffix("/RaceData.json") { data = Self.races; code = 200 }
        else if path.hasSuffix("/SignalData.json"), !Self.signals.isEmpty { data = Self.signals; code = 200 }
        else if path.hasSuffix("/TicketPlanShadow.json"), !Self.shadow.isEmpty { data = Self.shadow; code = 200 }
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
