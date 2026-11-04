import Foundation
import UIKit

// Added to the app target ONLY in the disposable CI checkout, never in the ZIP.
enum KeibaAIUITestTransport {
    static func enableIfRequested() {
        guard ProcessInfo.processInfo.environment["KEIBAAI_UI_FIXTURE"] == "1" else { return }
        let data = NSDataAsset(name: "RaceData")!.data
        let dateFormatter=DateFormatter();dateFormatter.dateFormat="yyyy-MM-dd";dateFormatter.timeZone=TimeZone(identifier:"Asia/Tokyo")
        let today=dateFormatter.string(from:Date())
        let researchFixture = #"""
        __BET_RESEARCH_FIXTURE__
        """#
        let simulationFixture = #"""
        __SIMULATOR_FIXTURE__
        """#
        PhoneFixtureProtocol.simulations = Data(simulationFixture.utf8)
        let verification: [String:Any] = ["status":"awaiting_live_race","matchedRaces":0,
            "message":"実開催の保存・結果照合が一巡する確認はこれからです。"]
        PhoneFixtureProtocol.simulatorRecording = try! JSONSerialization.data(withJSONObject:[
            "generatedAt":today+"T09:50:00+09:00","targetRaceDate":today,"expectedScheduleAvailable":true,
            "counts":["failed":1,"result_waiting":1],"issues":[String](),"liveVerification":verification,
            "races":[["raceID":"202605040601","raceDate":today,"venue":"東京","raceNumber":1,
                "raceName":"フレーム操作確認","startTime":"10:00","state":"result_waiting",
                "label":"予測保存済・結果待ち","reason":"発走前の公開履歴を確認済みです。","durableConfirmed":true],
                ["raceID":"202605040602","raceDate":today,"venue":"東京","raceNumber":2,
                "raceName":"未生成の操作確認","startTime":"10:30","state":"failed",
                "label":"保存失敗","reason":"保存処理に失敗しました。発走前は再試行します。","durableConfirmed":false]]])
        PhoneFixtureProtocol.simulatorLearning = try! JSONSerialization.data(withJSONObject:[
            "generatedAt":today+"T09:50:00+09:00","status":"candidate_in_forward_validation",
            "activeModelID":"race-simulator-v02-baseline","candidateModelID":"fixture-candidate",
            "settledProspectiveRaces":30,"liveVerification":verification,"decisionHistory":[Any](),
            "policy":["min_training_races":30,"min_validation_races":30,"min_validation_days":2,
                "min_condition_races":6,"min_condition_days":2,"min_monitor_races":30,"min_monitor_days":2,
                "rollback_relative_degradation":0.05],
            "conditionComparisons":[["key":"surface:turf","label":"芝","races":12,"days":2,
                "status":"pass","relativeImprovement":0.04],
                ["key":"surface:dirt","label":"ダート","races":2,"days":1,"status":"insufficient_data"]]])
        PhoneFixtureProtocol.simulatorOperationsFail = ProcessInfo.processInfo.environment["KEIBAAI_UI_SIM_OPS_ERROR"] == "1"
        var research = try! JSONSerialization.jsonObject(with:Data(researchFixture.replacingOccurrences(of:"2026-10-17",with:today).replacingOccurrences(of:"WIN5-20261017",with:"WIN5-"+today.replacingOccurrences(of:"-",with:"")).utf8)) as! [String:Any]
        var researchRows=research["races"] as! [[String:Any]]
        for index in researchRows.indices {
            if researchRows[index]["betType"] as? String == "trifecta" {
                researchRows[index]["raceName"]="オクトーバー特別・秋季研究レース"
                var cs=researchRows[index]["candidates"] as! [[String:Any]]
                cs[0]["probabilityPct"]=0.000123
                cs[0]["label"]="1番 アスクセクシーモア → 2番 ロングネームホース → 3番 オクトーバー"
                researchRows[index]["candidates"]=cs
            }
            if researchRows[index]["betType"] as? String == "frame_quinella" {
                researchRows[index]["status"]="held";researchRows[index]["statusLabel"]="条件不足で保留"
                researchRows[index]["reason"]="公式出馬表の枠番号を確認できないため保留しています。"
                researchRows[index]["candidates"]=[];researchRows[index]["isSaved"]=false
            }
        }
        if ProcessInfo.processInfo.environment["KEIBAAI_UI_BET_EMPTY"] == "1" {
            researchRows=[]
            var types=research["catalog"] as! [[String:Any]]
            for index in types.indices {
                var counts=types[index]["cumulative"] as! [String:Int]
                for key in counts.keys { counts[key]=0 };types[index]["cumulative"]=counts
            }
            research["catalog"]=types
        }
        research["races"]=researchRows
        PhoneFixtureProtocol.research=try! JSONSerialization.data(withJSONObject:research)
        PhoneFixtureProtocol.researchFails=ProcessInfo.processInfo.environment["KEIBAAI_UI_BET_ERROR"] == "1"
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
            if ProcessInfo.processInfo.environment["KEIBAAI_UI_AXIS"] == "1" {
                var hs = Array((sample["horses"] as! [[String:Any]]).prefix(8))
                let ai = [60.0,55,45,35,30,40,25,10], market = [70.0,60,50,35,30,25,20,10]
                for i in hs.indices { hs[i]["aiTop3Probability"] = ai[i]; hs[i]["marketTop3Probability"] = market[i] }
                sample["horses"] = hs; other["horses"] = hs
                PhoneFixtureProtocol.races = try! JSONSerialization.data(withJSONObject:[sample,other])
            }
            let evaluation: [String:Any] = ["status":"odds_connected","oddsConnected":true,
                "capturedAtJST":day+" 09:50:00","pairs":[pair(1,2,30,4),pair(1,3,20,6),pair(2,3,10,12)]]
            var signals: [[String:Any]] = [sample,other].map { r in
                ["raceID":r["raceID"]!,"raceDate":day,"startTime":r["startTime"]!,
                 "eligible":true,"wideEvaluation":evaluation]
            }
            if ProcessInfo.processInfo.environment["KEIBAAI_UI_AXIS"] == "1" {
                func axisPair(_ a: Int, _ prob: Double, _ odds: Double) -> [String:Any] {
                    ["number1":a,"name1":horses[a-1]["name"]!,"number2":6,"name2":horses[5]["name"]!,
                     "probabilityPct":prob,"oddsLow":odds,"oddsHigh":odds+1,"expectedValue":prob/100*odds]
                }
                let node: [String:Any] = ["schemaVersion":1,"ruleVersion":"wide-axis-v1-20261014","status":"ready",
                    "message":"ワイド向けの穴軸・相手を評価しました。", "axis":["number":6,"name":horses[5]["name"]!,
                        "aiTop3Pct":40.0,"marketTop3Pct":25.0,"marketTop3Rank":6],
                    "pairs":[axisPair(1,28,4.1),axisPair(2,25,5),axisPair(3,20,6)],"totalYen":300,
                    "minimumSingleHitReturnYen":410.0,"capturedAtJST":day+" 09:50:00"]
                for i in signals.indices { signals[i]["wideAxisEvaluation"] = node }
                PhoneFixtureProtocol.axisShadow = try! JSONSerialization.data(withJSONObject:["schemaVersion":1,
                    "ruleVersion":"wide-axis-v1-20261014","generatedAtJST":day+" 09:50:00",
                    "counts":["recordedRaces":2,"held":0,"awaitingResults":1,"matched":1,"entrantChanged":0,"invalid":0,"selectedBets":3],
                    "returnIndex":1.1,"autoBetEnabled":false,"moneyAllocationEnabled":false])
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
        if ProcessInfo.processInfo.environment["KEIBAAI_UI_SIMULATOR"] == "1" {
            sample["raceDate"] = today
            sample["raceName"] = "フレーム操作確認"
            sample["course"] = "芝1600m"
            var horses = Array((sample["horses"] as! [[String:Any]]).prefix(3))
            for i in horses.indices {
                horses[i]["number"] = i + 1
                horses[i]["name"] = "再生確認\(i + 1)"
                horses[i]["aiProbability"] = [60.0, 30.0, 10.0][i]
            }
            sample["horses"] = horses
            var pending = sample
            pending["raceID"] = "202605040602"
            pending["raceNumber"] = 2
            pending["raceName"] = "未生成の操作確認"
            PhoneFixtureProtocol.races = try! JSONSerialization.data(withJSONObject: [sample, pending])
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
    static var axisShadow = Data()
    static var research = Data()
    static var simulations = Data()
    static var researchFails = false
    static var simulatorRecording = Data()
    static var simulatorLearning = Data()
    static var simulatorOperationsFail = false
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let path = request.url!.path
        let data: Data
        let code: Int
        if path.hasSuffix("/RaceData.json") { data = Self.races; code = 200 }
        else if path.hasSuffix("/RaceSimulationData.json") { data = Self.simulations; code = 200 }
        else if path.hasSuffix("/RaceSimulatorRecordingStatus.json") { data = Self.simulatorRecording; code = Self.simulatorOperationsFail ? 503 : 200 }
        else if path.hasSuffix("/RaceSimulatorLearning.json") { data = Self.simulatorLearning; code = Self.simulatorOperationsFail ? 503 : 200 }
        else if path.hasSuffix("/SignalData.json"), !Self.signals.isEmpty { data = Self.signals; code = 200 }
        else if path.hasSuffix("/WideAxisShadow.json"), !Self.axisShadow.isEmpty { data = Self.axisShadow; code = 200 }
        else if path.hasSuffix("/TicketPlanShadow.json"), !Self.shadow.isEmpty { data = Self.shadow; code = 200 }
        else if path.hasSuffix("/ScheduleData.json") { data = Self.schedule; code = 200 }
        else if path.hasSuffix("/BetResearchData.json") { data=Self.research;code=Self.researchFails ? 503 : 200 }
        else { data = Data(); code = 404 }
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: code,
                           httpVersion: nil, headerFields: ["Content-Type": "application/json"])!,
                           cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() { }
}
