import Foundation

private final class BetTransport: URLProtocol {
    static var respond: (URLRequest) -> (Int,Data)? = { _ in nil }
    static var requests: [URLRequest] = []
    static var held: BetTransport?
    override class func canInit(with request:URLRequest) -> Bool { true }
    override class func canonicalRequest(for request:URLRequest) -> URLRequest { request }
    override func startLoading() {
        Self.requests.append(request)
        if let (code,body) = Self.respond(request) { finish(code,body) } else { Self.held = self }
    }
    override func stopLoading() { if Self.held === self { Self.held = nil } }
    func finish(_ code:Int,_ body:Data) {
        client?.urlProtocol(self,didReceive:HTTPURLResponse(url:request.url!,statusCode:code,httpVersion:nil,headerFields:nil)!,cacheStoragePolicy:.notAllowed)
        client?.urlProtocol(self,didLoad:body);client?.urlProtocolDidFinishLoading(self)
    }
}

@main
struct BetResearchChecks {
    static func check(_ condition:Bool,_ message:String) { if !condition { fatalError(message) } }
    static func bytes(_ value:[String:Any]) throws -> Data { try JSONSerialization.data(withJSONObject:value) }
    @MainActor static func waitFor(_ condition:()->Bool) async throws {
        for _ in 0..<500 { if condition() { return };try await Task.sleep(nanoseconds:10_000_000) }
        fatalError("held reference request did not start")
    }
    @MainActor static func main() async throws {
        DispatchQueue.global().asyncAfter(deadline:.now()+60) { fatalError("reference store test timed out") }
        let body = try Data(contentsOf:URL(fileURLWithPath:"/tmp/bet-research-contract.json"))
        let source = try JSONSerialization.jsonObject(with:body) as! [String:Any]
        let decoded = try JSONDecoder().decode(KeibaAIBetResearchData.self,from:body)
        try decoded.validate()
        check(decoded.catalog.count == 10 && decoded.races.count == 10,"actual Python projection includes every pool")
        check(decoded.races.allSatisfy { $0.candidates.count == 3 },"saved candidates for all ten types")
        check(decoded.races.first{$0.betType == "support"}!.candidates.allSatisfy{$0.unitYen == 200},"package costs 200")
        check(decoded.races.first{$0.betType == "win5"}!.candidates[0].label.components(separatedBy:" → ").count == 5,"five legs")
        check(KeibaAIBetResearchCandidate.percent(22.4) == "22.40%","decimal remains visible")
        check(KeibaAIBetResearchCandidate.percent(0.000123) == "0.000123%","tiny probability remains visible")
        check(KeibaAIBetResearchCandidate.percent(0.00000001) == "0.000001%未満","do not round tiny probability to zero")
        let mutations: [(inout [String:Any])->Void] = [
            { $0["formalRecommendationsEnabled"] = true },
            { $0["autoBetEnabled"] = true },
            { $0["raceDate"] = "2026-02-30" },
            { $0["catalog"] = [] },
            { value in var rows=value["races"] as! [[String:Any]];rows[0]["status"]="invalid";value["races"]=rows },
            { value in var rows=value["races"] as! [[String:Any]];var cs=rows[0]["candidates"] as! [[String:Any]];cs[0]["probabilityPct"]=101;rows[0]["candidates"]=cs;value["races"]=rows }
        ]
        for mutate in mutations {
            var value=source;mutate(&value)
            do { let v=try JSONDecoder().decode(KeibaAIBetResearchData.self,from:bytes(value));try v.validate();fatalError("invalid reference contract accepted") } catch { }
        }
        print("BET_REFERENCE_CONTRACT_OK: ten Python types, immutable candidates, tiny decimals and rejected invalid payloads")

        let config=URLSessionConfiguration.ephemeral;config.protocolClasses=[BetTransport.self]
        let session=URLSession(configuration:config);defer { session.invalidateAndCancel() }
        let store=KeibaAIBetResearchStore(session:session)
        BetTransport.respond={_ in (200,body)}
        await store.load(date8:"20261017")
        check(store.data?.races.count == 10 && store.errorMessage == nil,"initial load")
        let request=BetTransport.requests.last!
        check(request.url!.path.hasSuffix("/BetResearchData.json") && request.timeoutInterval == 20 && request.cachePolicy == .reloadIgnoringLocalCacheData,"actual request")
        check(URLComponents(url:request.url!,resolvingAgainstBaseURL:false)!.queryItems!.contains{$0.name == "t"},"cache bust")
        for (code,bad) in [(404,body),(503,Data()),(200,Data("{broken".utf8))] {
            BetTransport.respond={_ in (code,bad)};await store.load(date8:"20261017")
            check(store.data?.races.count == 10 && store.errorMessage != nil && !store.isLoading,"failure keeps data with visible error")
        }
        BetTransport.respond={_ in (200,body)};await store.load(date8:"20261017")
        check(store.errorMessage == nil,"recovery")
        let count=BetTransport.requests.count;BetTransport.respond={_ in nil}
        let first=Task { await store.load(date8:"20261017") }
        try await waitFor { BetTransport.held != nil }
        await store.load(date8:"20261017")
        check(BetTransport.requests.count == count+1,"duplicate refresh prevented")
        // A new date completes first. An old delayed response must not replace it.
        let held=BetTransport.held!;BetTransport.held=nil
        var newer=source;newer["raceDate"]="2026-10-18";newer["races"]=[]
        let newBytes=try bytes(newer);BetTransport.respond={_ in (200,newBytes)}
        await store.load(date8:"20261018");held.finish(200,body);await first.value
        check(store.data?.raceDate == "2026-10-18" && !store.isLoading,"old date cannot overwrite new selection")
        BetTransport.respond={_ in (500,Data())};await store.load(date8:"20261019")
        check(store.data == nil && store.errorMessage != nil,"date change clears previous rows before failure")

        var root=source;root["raceDate"]="2026-10-18";root["races"]=[]
        let rootBody=try bytes(root)
        BetTransport.respond={request in (200,request.url!.path.contains("/reports/20261017/") ? body : rootBody)}
        await store.load(date8:"20261017")
        check(store.data?.raceDate == "2026-10-17","dated history uses exact-date projection")
        BetTransport.respond={_ in (200,rootBody)};await store.load(date8:"20261017")
        check(store.data?.raceDate == "2026-10-17" && store.errorMessage != nil,"wrong-day history rejected")
        BetTransport.respond={_ in nil}
        let cancelled=Task { await store.load(date8:"20261017") }
        try await waitFor { BetTransport.held != nil };cancelled.cancel();await cancelled.value
        check(!store.isLoading && store.errorMessage == nil,"cancellation releases loading without false failure")
        let empty=KeibaAIBetResearchStore(session:session);BetTransport.respond={_ in (404,Data())}
        await empty.load(date8:"20261017")
        check(empty.data == nil && empty.errorMessage != nil,"initial 404 cannot become zero-record success")
        print("BET_REFERENCE_STORE_OK: real URLSession store, failures, recovery, exact date, duplicate guard, stale-response guard, cancellation")
    }
}
