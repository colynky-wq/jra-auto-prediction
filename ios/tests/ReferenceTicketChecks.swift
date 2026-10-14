import Foundation

private final class ShadowProtocol: URLProtocol {
    static var bytes = Data()
    static var code = 200
    static var requests = [URLRequest]()
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        Self.requests.append(request)
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: Self.code,
                httpVersion: nil, headerFields: nil)!, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Self.bytes)
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

@main
struct ReferenceTicketChecks {
    static func check(_ value: Bool, _ message: String) {
        precondition(value, message)
    }
    static func pair(_ a: Int, _ b: Int, _ p: Double, _ odds: Double) -> KeibaAIReferencePair {
        KeibaAIReferencePair(number1: a, name1: "馬\(a)", number2: b, name2: "馬\(b)",
            probabilityPct: p, oddsLow: odds, oddsHigh: odds + 1, expectedValue: (p / 100 * odds * 10000).rounded() / 10000)
    }
    static func race(_ id: String, time: String = "10:00", date: String = "2026-10-17",
                     capture: String = "2026-10-17 09:50:00", pairs: [KeibaAIReferencePair]? = nil,
                     connected: Bool = true) -> KeibaAIReferenceRaceInput {
        KeibaAIReferenceRaceInput(raceID: id, raceDate: date, startTime: time, eligible: true,
            horses: [1:"馬1",2:"馬2",3:"馬3",4:"馬4"], oddsConnected: connected,
            capturedAtJST: capture, pairs: pairs ?? [pair(1,2,30,4),pair(1,3,20,6),pair(2,3,10,12)])
    }
    static func plans(_ rows: [KeibaAIReferenceRaceInput], _ p: KeibaAIReferenceProfile = .balanced,
                      budget: Int = 3000, cap: Int? = nil) -> [KeibaAIReferenceRacePlan] {
        KeibaAIReferencePlanner.plans(inputs: rows, date: "20261017", profile: p, perRace: budget, dailyLimit: cap)
    }
    @MainActor
    static func main() async throws {
        let first = plans([race("tokyo")])[0]
        check(first.pairs.map(\.id) == ["1-2","1-3"] && first.totalYen == 200 && first.remainingYen == 2800,
              "100-yen preview leaves the rest unused; deterministic tie ordering")
        check(plans([race("a")], .stable)[0].totalYen == 100, "stable max one")
        check(plans([race("a")], .highValue)[0].totalYen == 300, "high value max three")
        let capped = plans([race("kyoto",time:"10:05"),race("tokyo")],budget:3000,cap:300)
        check(capped.map(\.id) == ["tokyo","kyoto"] && capped.map(\.totalYen) == [200,100],
              "daily limit shared across venues; chronology does not depend on input order")
        check(plans([race("a"),race("b",time:"10:05")],cap:0).allSatisfy { $0.totalYen == 0 }, "zero daily limit")
        check(plans([race("a")],budget:0)[0].totalYen == 0, "zero race budget")
        check(plans([race("a")],budget:100)[0].totalYen == 100, "budget smaller than candidate count")
        check(plans([race("a")],budget:199)[0].totalYen == 0, "invalid unit is held")
        check(plans([race("a")],budget:Int.max-Int.max%100,cap:Int.max-Int.max%100)[0].totalYen == 200,
              "huge valid budgets do not overflow")
        check(plans([race("a",pairs:[pair(1,2,10,5)])])[0].totalYen == 0, "no qualifying value is a skip")
        check(plans([race("a",connected:false)])[0].totalYen == 0, "no real odds means no preview")
        check(plans([race("a",date:"2026-10-18")])[0].totalYen == 0, "different day held")
        check(plans([race("a",capture:"2026-10-17 10:00:00")])[0].totalYen == 0, "post-time capture rejected")
        check(plans([race("a",capture:"2026-10-16 09:50:00")])[0].totalYen == 0, "previous-day capture rejected")
        check(plans([race("a",capture:"bad")])[0].totalYen == 0, "unknown capture held")
        check(plans([race("a"),race("a")]).allSatisfy { $0.totalYen == 0 }, "duplicate race cannot spend twice")
        check(plans([race("a",pairs:[pair(1,2,30,4),pair(2,1,30,4)])])[0].totalYen == 0, "duplicate unordered pair held")
        let bad = KeibaAIReferencePair(number1:1,name1:"別馬",number2:2,name2:"馬2",probabilityPct:30,
                                      oddsLow:4,oddsHigh:5,expectedValue:1.2)
        check(plans([race("a",pairs:[bad])])[0].totalYen == 0,"entrant name mismatch held")
        for p in [pair(1,2,Double.nan,4),pair(1,2,101,4),pair(1,2,30,Double.infinity)] {
            check(plans([race("a",pairs:[p])])[0].totalYen == 0,"invalid number held")
        }
        check(KeibaAIReferencePlanner.date8("2026-02-30") == nil, "invalid calendar day rejected")
        let boundary = KeibaAIReferencePair(number1:1,name1:"馬1",number2:2,name2:"馬2",probabilityPct:30,
            oddsLow:3.665,oddsHigh:4,expectedValue:1.100)
        check(plans([race("boundary",pairs:[boundary])])[0].pairs.isEmpty,
              "rounded display EV must not promote a below-threshold validation candidate")
        let bytes = try Data(contentsOf: URL(fileURLWithPath:"reports/TicketPlanShadow.json"))
        let decoded = try KeibaAITicketShadowData.decode(bytes)
        check(decoded.profiles.count == 3 && !decoded.moneyAllocationEnabled,"real summary decodes")
        var obj = try JSONSerialization.jsonObject(with:bytes) as! [String:Any]
        obj["moneyAllocationEnabled"] = true
        do { _ = try KeibaAITicketShadowData.decode(JSONSerialization.data(withJSONObject:obj)); preconditionFailure("enabled payload accepted") }
        catch { }
        obj["moneyAllocationEnabled"] = false
        obj["profiles"] = [:]
        do { _ = try KeibaAITicketShadowData.decode(JSONSerialization.data(withJSONObject:obj)); preconditionFailure("missing profiles accepted") }
        catch { }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ShadowProtocol.self]
        let store = KeibaAITicketShadowStore(session:URLSession(configuration:configuration))
        ShadowProtocol.code = 500
        await store.load()
        check(store.data == nil && store.errorMessage != nil,"first failure is unknown, not zero")
        ShadowProtocol.code = 200; ShadowProtocol.bytes = bytes
        await store.load()
        check(store.data != nil && store.errorMessage == nil,"HTTP recovery shows actual data")
        ShadowProtocol.bytes = Data("{}".utf8)
        await store.load()
        check(store.data?.generatedAtJST == decoded.generatedAtJST && store.errorMessage != nil,
              "malformed refresh keeps the last value and explains staleness")
        check(ShadowProtocol.requests.allSatisfy { $0.httpMethod == "GET" && $0.url!.query!.contains("t=") },
              "transport read-only and cache-busted")
        print("REFERENCE_TICKET_OK: budgets, daily cross-venue cap, skips, source validity, real summary, failed refresh and recovery")
    }
}
