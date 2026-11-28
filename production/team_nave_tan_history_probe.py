"""Offline decoder/contract probe for Team-Nave's documented TAN history format.

This module makes ZERO network calls and never reads credentials.
Historical TAN response documentation identifies results.tan as JSON strings
with an rlsdttm release timestamp. WIDE response schema is not confirmed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
import json
import math
from pathlib import Path
import re

JST=timezone(timedelta(hours=9))
STAMP=re.compile(r"^20[0-9]{10}$")
HORSE=re.compile(r"^[0-9]{2}$")


def strict_pairs(pairs):
    result={}
    for key,val in pairs:
        if key in result:
            raise ValueError("duplicate field in vendor JSON")
        result[key]=val
    return result


def strict_json(source):
    if isinstance(source,bytes):
        source=source.decode("utf-8")
    if not isinstance(source,str):
        raise ValueError("vendor JSON must be a string")
    def reject_constant(value):
        raise ValueError("nonfinite number in vendor JSON")
    return json.loads(source,object_pairs_hook=strict_pairs,
                      parse_constant=reject_constant)


def decode_tan_history(raw_response, *, at_jst, max_age_minutes=5):
    """Report market quote evidence only if an actual release timestamp exists."""
    if not isinstance(raw_response,dict) or type(raw_response.get("ret")) is not int:
        raise ValueError("missing vendor success code")
    if raw_response["ret"]!=0:
        raise ValueError("vendor returned a non-success response")
    result=raw_response.get("results")
    if not isinstance(result,dict):
        raise ValueError("missing vendor results")
    body=result.get("tan")
    samples=[body] if isinstance(body,(dict,str)) else body
    if not isinstance(samples,list) or not samples:
        raise ValueError("missing documented TAN history entries")
    if not isinstance(at_jst,datetime) or at_jst.tzinfo is None:
        raise ValueError("observation time must be timezone aware")
    target=at_jst.astimezone(JST)
    best=None
    for sample in samples:
        snapshot=strict_json(sample) if isinstance(sample,(str,bytes)) else sample
        if not isinstance(snapshot,dict):
            raise ValueError("invalid TAN snapshot")
        stamp=snapshot.get("rlsdttm")
        if not isinstance(stamp,str) or not STAMP.fullmatch(stamp):
            continue
        try:
            released=datetime.strptime(stamp,"%Y%m%d%H%M").replace(tzinfo=JST)
        except ValueError:
            continue
        if released>target:
            continue
        prices={}
        for key,value in snapshot.items():
            if key=="rlsdttm":
                continue
            if not isinstance(key,str) or not HORSE.fullmatch(key):
                raise ValueError("TAN horse key not 2-digit")
            horse=int(key)
            if not 1<=horse<=18 or horse in prices:
                raise ValueError("invalid or duplicate horse")
            if type(value) not in (str,int,float):
                raise ValueError("TAN odds must be numeric")
            price=float(value)
            if not math.isfinite(price) or price<1:
                raise ValueError("invalid TAN odds")
            prices[horse]=price
        if not prices:
            raise ValueError("TAN snapshot has no valid price")
        entry={"releasedAtJST":released.isoformat(timespec="minutes"),
               "ageMinutes":round((target-released).total_seconds()/60,3),
               "horses":len(prices),"prices":prices}
        if best is None or released>best[0]:
            best=(released,entry)
    if best is None:
        raise ValueError("no timestamped historical pre-observation TAN quote")
    evidence=best[1]
    evidence["withinResearchMaxAge"]=evidence["ageMinutes"]<=max_age_minutes
    evidence["vendorWIDEFormatVerified"]=False
    evidence["sourceAuthenticityVerified"]=False
    evidence["oddsUsedForLiveTrading"]=False
    return evidence


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture",type=Path,required=True,
                        help="operator-provided, appropriately licensed TAN API response JSON")
    parser.add_argument("--observed-at-jst",required=True,
                        help="ISO-8601 observation with +09:00 offset")
    args=parser.parse_args()
    at=datetime.fromisoformat(args.observed_at_jst)
    if at.tzinfo is None:
        parser.error("the timestamp must include a timezone offset")
    data=strict_json(args.fixture.read_bytes())
    evidence=decode_tan_history(data,at_jst=at)
    print(json.dumps(evidence,ensure_ascii=False,sort_keys=True,
                     indent=2,allow_nan=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
